"""Desktop launcher: runs SFS as the backend sidecar of the desktop app.

The desktop shell (Tauri) starts this with the OS app-data directory:

    sfs-backend --data-dir <app data dir>

It points the existing settings (PROJECTS_DIR, FRAME_CACHE_DIR, …) at that
directory through environment variables, so backend configuration is the same
as the server edition. It creates a SECRET_KEY on first launch (owner-only
file), binds a free port on 127.0.0.1 only, and prints one machine-readable
line when the server is ready:

    SFS_READY port=53817

The shell waits for that line before loading http://127.0.0.1:<port>/. When
the shell goes away, stdin closes and the server shuts down.

Nothing here runs in the server edition.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
from pathlib import Path

READY_PREFIX = "SFS_READY"
SECRET_KEY_FILE = "secret.key"


def ensure_secret_key(path: Path) -> str:
    """The Fernet key that encrypts saved connection passwords. Created once,
    readable by the owner only; losing it makes saved passwords undecryptable."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    from cryptography.fernet import Fernet

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key().decode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    return key


def desktop_environment(data_dir: Path, environ: dict | None = None) -> dict[str, str]:
    """The environment variables that point SFS at `data_dir`. Values already
    set in `environ` win, so a developer can still override any of them."""
    environ = os.environ if environ is None else environ
    data_dir = data_dir.resolve()
    defaults = {
        "PROJECTS_DIR": str(data_dir / "projects"),
        "FRAME_CACHE_DIR": str(data_dir / "frame_cache"),
        "REPORTS_DIR": str(data_dir / "reports_data"),
        "PERSIST_DB": str(data_dir / "reports_data" / "persist.duckdb"),
        "METADATA_DB_URL": "sqlite+aiosqlite:///" + (data_dir / "flow_studio.db").as_posix(),
        "SERVE_STATIC": "true",
        "DEBUG": "false",
        "HOST": "127.0.0.1",
        "SFS_DESKTOP": "1",
        "SFS_DATA_DIR": str(data_dir),
        # Optional settings file (e.g. ANTHROPIC_API_KEY); never the .env of
        # the folder the app happened to be launched from.
        "SFS_ENV_FILE": str(data_dir / ".env"),
    }
    if getattr(sys, "frozen", False):
        # PyInstaller unpacks bundled data (the built frontend) under _MEIPASS
        defaults["STATIC_DIR"] = str(Path(getattr(sys, "_MEIPASS")) / "static")
    env = {k: v for k, v in defaults.items() if not environ.get(k)}
    if not environ.get("SECRET_KEY"):
        env["SECRET_KEY"] = ensure_secret_key(data_dir / SECRET_KEY_FILE)
    return env


def bind_local_socket(port: int = 0) -> socket.socket:
    """A listening socket on 127.0.0.1 (never all interfaces); port 0 = any free port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def ready_line(port: int) -> str:
    return f"{READY_PREFIX} port={port}"


def _exit_when_stdin_closes(server) -> None:
    """The shell holds our stdin open; EOF means it has gone, so stop."""
    def watch():
        try:
            while sys.stdin.buffer.read(1):
                pass
        except Exception:  # noqa: BLE001 — no usable stdin: keep running
            return
        server.should_exit = True
    threading.Thread(target=watch, name="sfs-stdin-watch", daemon=True).start()


def _log_to_stderr() -> None:
    """stderr is the shell's logs/backend.log: start each session with a
    header, timestamp warnings and errors, and dump native crashes (a segfault
    in a data library) with their Python stack."""
    import faulthandler
    import logging
    import platform
    import time

    print(f"\n=== SFS backend starting {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"(Python {platform.python_version()}, {platform.platform()}) ===", file=sys.stderr, flush=True)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    faulthandler.enable(file=sys.stderr)
    # Diagnostics for hangs (the CI smoke test sets it): every thread's stack
    # to stderr every N seconds.
    if os.environ.get("SFS_STACK_DUMP_SECONDS", "").isdigit():
        faulthandler.dump_traceback_later(int(os.environ["SFS_STACK_DUMP_SECONDS"]), repeat=True, file=sys.stderr)


def _load_native_libraries() -> None:
    """Load SciPy's compiled BLAS/LAPACK on the main thread before serving.

    Node code runs in worker threads, and on Windows the first load of
    SciPy's OpenBLAS DLL from a worker thread never returns (seen in the
    frozen build: stuck in create_module for scipy.linalg._fblas). NumPy's
    copy is already loaded here, through pandas. Loading SciPy's now costs a
    fraction of a second and makes `import scipy.stats` in a node safe."""
    try:
        import scipy.linalg  # noqa: F401
    except ImportError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sfs-backend", description="SFS desktop backend")
    parser.add_argument("--data-dir", required=True, type=Path, help="app data directory")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    parser.add_argument("--no-stdin-watch", action="store_true",
                        help="keep running when stdin closes (for running by hand)")
    args = parser.parse_args(argv)

    # Settings are read when app.core.config is imported, so set them first.
    os.environ.update(desktop_environment(args.data_dir))
    _log_to_stderr()
    _load_native_libraries()

    import asyncio

    import uvicorn

    from app.core.app_factory import create_app

    sock = bind_local_socket(args.port)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="warning", access_log=False))
    if not args.no_stdin_watch:
        _exit_when_stdin_closes(server)

    async def announce():
        while not server.started:
            if server.should_exit:
                return
            await asyncio.sleep(0.02)
        print(ready_line(port), flush=True)

    async def run():
        task = asyncio.create_task(announce())
        await server.serve(sockets=[sock])
        task.cancel()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
