"""
Isolated plugin runtime.

Runs a plugin's work in a **separate Python interpreter** — its own virtual
environment with its own pinned dependencies — and exchanges data as Apache
Arrow files on disk. The SFS process never imports the heavy library, so a
plugin that needs an old scikit-learn, or PyCaret's opinionated pins, cannot
disturb the core environment.

This is the mechanism behind "add ML capabilities without breaking your ETL
environment". It is deliberately boring: files and a subprocess, no RPC server,
no shared memory, nothing to keep alive between runs.

## Protocol

SFS creates a temporary job directory and writes:

    <job>/input.arrow        first upstream frame (Arrow IPC)   [if any]
    <job>/input2.arrow, …    additional upstream frames
    <job>/request.json       {code, params, inputs[], output, meta}

then invokes:

    <venv-python> <runner.py> <job>/request.json

The runner reads the frames, does its work, writes `<job>/output.arrow`, and
prints a single JSON line to stdout:

    {"ok": true, "rows": 1234, "log": "optional text"}
    {"ok": false, "error": "message"}

Anything else the runner prints is captured and surfaced as node logs, so
`print()` inside a plugin still reaches the user.

The runner script ships *with the plugin*, not with SFS — it is the plugin
author's code, executed by the plugin's own interpreter.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc

from app.core.arrow_files import ipc_writer

log = logging.getLogger("sfs.runtime")

DEFAULT_TIMEOUT = 1800  # 30 minutes


class IsolatedRuntimeError(RuntimeError):
    """Raised when an isolated run fails. The message is shown on the node."""


def venvs_dir() -> Path:
    """Where plugin virtual environments live.

    Override with SFS_PLUGIN_VENVS_DIR. In Docker this should point at a
    directory inside the image (or a mounted volume) holding one venv per
    plugin, e.g. /opt/sfs-venvs/pycaret.
    """
    return Path(os.environ.get("SFS_PLUGIN_VENVS_DIR", "./plugin_venvs")).expanduser()


def venv_python(venv: str | os.PathLike) -> str:
    """Resolve the interpreter for a venv given either a bare name (looked up
    under venvs_dir()) or an explicit path. Falls back to the SFS interpreter
    only when the name is "self", which is useful for testing a runner without
    building a venv first."""
    if str(venv) == "self":
        return sys.executable

    p = Path(venv)
    if not p.is_absolute() and not p.exists():
        p = venvs_dir() / str(venv)

    for candidate in (p / "bin" / "python", p / "Scripts" / "python.exe", p):
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    raise IsolatedRuntimeError(
        f"No Python interpreter found for plugin environment '{venv}'. "
        f"Looked under {p}. Create it with: python -m venv {p} && "
        f"{p}/bin/pip install <your dependencies>"
    )


def available_venvs() -> list[str]:
    """Names of plugin environments that look usable — for diagnostics and
    /api/capabilities."""
    d = venvs_dir()
    if not d.is_dir():
        return []
    out = []
    for child in sorted(d.iterdir()):
        if (child / "bin" / "python").exists() or (child / "Scripts" / "python.exe").exists():
            out.append(child.name)
    return out


def _write_frame(df_or_table, path: Path) -> None:
    table = df_or_table
    if not isinstance(table, pa.Table):
        table = pa.Table.from_pandas(df_or_table, preserve_index=False)
    with ipc_writer(path, table.schema) as w:
        w.write_table(table)


def _read_frame(path: Path) -> pa.Table:
    with ipc.open_file(path) as r:
        return r.read_all()


def run_isolated(
    *,
    venv: str,
    runner: str | os.PathLike,
    inputs: list = (),
    params: dict | None = None,
    code: str = "",
    meta: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    keep_job_dir: bool = False,
):
    """Execute `runner` inside `venv`, handing it `inputs` as Arrow files.

    Returns (arrow_table, info) where info holds the runner's JSON response
    plus any captured stdout/stderr. Raises IsolatedRuntimeError with a useful
    message on any failure — that message is what the user sees on the node.

    `inputs` may be pandas DataFrames or pyarrow Tables. An engine with
    input_mode="paths" can pass the cached Arrow file paths straight through
    instead (see `inputs_are_paths`).
    """
    python = venv_python(venv)
    runner_path = Path(runner)
    if not runner_path.exists():
        raise IsolatedRuntimeError(f"Runner script not found: {runner_path}")

    job = Path(tempfile.mkdtemp(prefix="sfs-iso-"))
    try:
        input_paths: list[str] = []
        for i, frame in enumerate(inputs or []):
            if isinstance(frame, (str, os.PathLike)):
                # already an Arrow file on disk — hand over the path as-is
                input_paths.append(str(frame))
                continue
            p = job / (f"input{i + 1}.arrow" if i else "input.arrow")
            _write_frame(frame, p)
            input_paths.append(str(p))

        output_path = job / "output.arrow"
        request = {
            "inputs": input_paths,
            "output": str(output_path),
            "params": params or {},
            "code": code,
            "meta": meta or {},
        }
        req_path = job / "request.json"
        req_path.write_text(json.dumps(request))

        try:
            proc = subprocess.run(
                [python, str(runner_path), str(req_path)],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise IsolatedRuntimeError(
                f"Plugin runtime '{venv}' timed out after {timeout}s."
            ) from None

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        # The response is the last JSON object printed; everything before it is
        # treated as plugin logs so print() still works for the author.
        response, logs = None, []
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    response = json.loads(line)
                    continue
                except json.JSONDecodeError:
                    pass
            if line:
                logs.append(line)

        if proc.returncode != 0 and response is None:
            tail = (stderr or stdout or "no output")[-1500:]
            raise IsolatedRuntimeError(
                f"Plugin runtime '{venv}' exited with code {proc.returncode}.\n{tail}"
            )
        if response is None:
            raise IsolatedRuntimeError(
                f"Plugin runner printed no JSON response.\n"
                f"stdout: {stdout[-800:]}\nstderr: {stderr[-800:]}"
            )
        if not response.get("ok"):
            raise IsolatedRuntimeError(
                response.get("error") or f"Plugin runtime '{venv}' reported failure."
            )

        if not output_path.exists():
            raise IsolatedRuntimeError(
                "Plugin runner reported success but wrote no output frame."
            )
        table = _read_frame(output_path)

        info = {
            "rows": response.get("rows", table.num_rows),
            "log": "\n".join(logs + ([response["log"]] if response.get("log") else [])),
            "stderr": stderr,
            "venv": venv,
        }
        if info["log"]:
            log.info("[plugin:%s] %s", venv, info["log"][:2000])
        return table, info
    finally:
        if not keep_job_dir:
            shutil.rmtree(job, ignore_errors=True)
