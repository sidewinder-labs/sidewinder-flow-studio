"""Smoke test for the frozen desktop backend (dist/sfs-backend/).

    python smoke_test.py dist/sfs-backend/sfs-backend[.exe]

Starts the executable the way the desktop shell does, waits for the readiness
line, then checks what can only break in a frozen build: the frontend is
served, every bundled engine is available, a flow runs through pandas, DuckDB,
Polars and the SQL engine (SQLGlot, on SQLite), the sample project runs (its
CSV is bundled data), and Explore compiles a chart query (the wasmtime native
library). Finally it closes stdin, as the shell does
on exit, and expects the backend to stop. Standard library only, so CI can run
it with any Python.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

READY_TIMEOUT = 180          # first start on a cold CI disk is slow (macOS scans the bundle)
RUN_TIMEOUT = 240           # first imports of SciPy and friends are slow on Windows
ENGINES = {"pandas", "duckdb", "polars", "sql", "ibis", "curvefit_fit", "curvefit_eval",
           "tm1_read", "tm1_load", "powerbi_refresh"}
CONNECTION_TYPES = {"tm1", "powerbi"}

GEN = """import numpy as np
from scipy import stats
rng = np.random.default_rng(0)
df = pd.DataFrame({"g": rng.choice(["a", "b", "c"], 300), "x": stats.norm.rvs(size=300, random_state=1)})
"""


def _has_frame(call_raw, pid: str, node_id: str) -> bool:
    return call_raw(f"/api/execute/frame/{pid}/{node_id}?limit=1") == 200


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", flush=True)
    raise SystemExit(1)


def main(exe: str) -> None:
    data = Path(tempfile.mkdtemp(prefix="sfs-smoke-"))
    t0 = time.monotonic()
    import os
    env = {**os.environ, "SFS_STACK_DUMP_SECONDS": "60"}     # thread dumps if something hangs
    proc = subprocess.Popen([exe, "--data-dir", str(data)], stdin=subprocess.PIPE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()

    # ── readiness handshake ──
    port = None
    lines: list[str] = []
    ready = threading.Event()

    def read_stdout():
        # Keep reading after the ready line: node code's print() goes to
        # stdout, and an undrained pipe (4 KB on Windows) blocks the backend.
        nonlocal port
        for line in proc.stdout:
            if port is None and line.startswith("SFS_READY port="):
                port = int(line.split("port=")[1])
                ready.set()
            else:
                lines.append(line)
        ready.set()
    threading.Thread(target=read_stdout, daemon=True).start()
    ready.wait(READY_TIMEOUT)
    if port is None:
        proc.kill()
        fail(f"no ready line after {time.monotonic() - t0:.0f}s\nstdout:\n{''.join(lines)}\n"
             f"stderr:\n{''.join(stderr[-60:])}")
    print(f"ready on port {port} in {time.monotonic() - t0:.1f}s", flush=True)
    base = f"http://127.0.0.1:{port}"

    def call(path: str, body: dict | None = None, method: str | None = None):
        req = urllib.request.Request(base + path, method=method or ("GET" if body is None else "POST"),
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode()
        except urllib.error.HTTPError as e:
            fail(f"{req.method} {path} -> {e.code}: {e.read().decode()[:500]}")

    def call_raw(path: str) -> int:
        try:
            with urllib.request.urlopen(base + path, timeout=30) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:                                    # noqa: BLE001
            return -1

    try:
        # ── the app and its capabilities ──
        if 'id="root"' not in call("/"):
            fail("/ does not serve the frontend")
        caps = call("/api/capabilities/")
        available = {e["name"] for e in caps["engines"] if e["available"]}
        if ENGINES - available:
            fail(f"engines missing or unavailable: {sorted(ENGINES - available)}")
        if not caps["features"].get("desktop") or caps["features"].get("reports"):
            fail(f"unexpected features: {caps['features']}")
        types = {t["dialect"] for t in caps.get("connection_types", [])}
        if CONNECTION_TYPES - types:
            fail(f"connection types missing: {sorted(CONNECTION_TYPES - types)}")
        print("capabilities ok:", sorted(available), "connection types:", sorted(types), flush=True)

        # ── a flow through every engine ──
        call("/api/connections/", {"name": "lite", "dialect": "sqlite", "database": str(data / "smoke.sqlite")})
        proj = call("/api/projects/", {"name": "Smoke test"})
        pid, flow = proj["id"], proj["flows"][0]
        trig = flow["nodes"][0]["id"]
        node = lambda i, d: {"id": i, "type": "processor", "position": {"x": 0, "y": 0}, "data": {"label": i, **d}}
        edge = lambda s, t: {"id": f"{s}-{t}", "source": s, "target": t, "sourceHandle": "out", "targetHandle": "in"}
        flow["nodes"] += [
            node("plain", {"engine": "pandas", "code": 'df = pd.DataFrame({"a": [1, 2, 3]})'}),
            node("gen", {"engine": "pandas", "code": GEN}),
            node("duck", {"engine": "duckdb", "code": "SELECT g, COUNT(*) AS n, AVG(x) AS mean FROM df GROUP BY g ORDER BY g"}),
            node("pol", {"engine": "polars", "code": 'df = df.with_columns((pl.col("n") * 2).alias("n2"))'}),
            node("sql", {"engine": "sql", "target_connection": "lite", "code": "SELECT 41 + 1 AS answer"}),
            # fast_read: the bundled ConnectorX must load (else it silently falls back)
            node("fr", {"engine": "pandas", "code":
                        'arrow, info = _fast_read_arrow("lite", sql="SELECT 41 + 1 AS answer")\n'
                        'assert info["path"] == "connectorx", info\n'
                        'df = arrow.to_pandas()'}),
            # Bundled database drivers: building an engine loads the dialect and its
            # driver (no server needed). pyodbc also needs libodbc on Linux.
            node("drivers", {"engine": "pandas", "code":
                             'urls = ["clickhouse+http://u:p@h:8123/default", "mysql+pymysql://u:p@h/db",\n'
                             '        "oracle+oracledb://u:p@h:1521/?service_name=x", "trino://u@h:8080/hive",\n'
                             '        "mssql+pyodbc://u:p@h:1433/db?driver=ODBC+Driver+18+for+SQL+Server"]\n'
                             'for u in urls:\n'
                             '    sa.create_engine(u).dialect.import_dbapi()\n'
                             'df = pd.DataFrame({"drivers": [len(urls)]})'}),
        ]
        flow["edges"] += [edge(trig, "drivers"), edge(trig, "plain"), edge("plain", "gen"), edge("gen", "duck"), edge("duck", "pol"), edge(trig, "sql"), edge("sql", "fr")]
        call(f"/api/projects/{pid}", {"flows": [flow]}, method="PUT")
        call("/api/execute/run", {"project_id": pid, "flow_id": flow["id"], "flow_state": flow})

        deadline = time.monotonic() + RUN_TIMEOUT
        while time.monotonic() < deadline:
            runs = call(f"/api/runs/?project_id={pid}")
            if runs and runs[0]["status"] != "running":
                break
            time.sleep(0.5)
        else:
            frames = {n["id"]: _has_frame(call_raw, pid, n["id"]) for n in flow["nodes"]}
            fail(f"flow did not finish in {RUN_TIMEOUT}s; runs={runs}; node outputs={frames}\n"
                 f"stdout tail:\n{''.join(lines[-30:])}")
        if runs[0]["status"] != "ok":
            fail(f"flow {runs[0]['status']}: {runs[0].get('error')}")
        pol = call(f"/api/execute/frame/{pid}/pol")
        if pol["total"] != 3 or "n2" not in {c["name"] for c in pol["schema"]}:
            fail(f"unexpected Polars output: {pol['schema']}, {pol['total']} rows")
        if call(f"/api/execute/frame/{pid}/sql")["rows"] != [{"answer": 42}]:
            fail("SQL engine result is wrong")
        if call(f"/api/execute/frame/{pid}/fr")["rows"] != [{"answer": 42}]:
            fail("fast_read result is wrong")
        if call(f"/api/execute/frame/{pid}/drivers")["rows"] != [{"drivers": 5}]:
            fail("a bundled database driver did not load")
        print(f"flow ok in {runs[0]['duration_ms']} ms (pandas, DuckDB, Polars, SQL, fast_read, 5 drivers)", flush=True)

        # ── first run: desktop info, and the sample project (its CSV is bundled data) ──
        info = call("/api/desktop/")
        if info["welcome_seen"] or not info["secret_key"].endswith("secret.key"):
            fail(f"unexpected desktop info: {info}")
        sample = call("/api/projects/from-example", {"example": "Sales pipeline (CSV)"})
        sflow = sample["flows"][0]
        call("/api/execute/run", {"project_id": sample["id"], "flow_id": sflow["id"], "flow_state": sflow})
        deadline = time.monotonic() + RUN_TIMEOUT
        while time.monotonic() < deadline:
            sruns = call(f"/api/runs/?project_id={sample['id']}")
            if sruns and sruns[0]["status"] != "running":
                break
            time.sleep(0.5)
        else:
            fail("sample project did not finish")
        if sruns[0]["status"] != "ok":
            fail(f"sample project {sruns[0]['status']}: {sruns[0].get('error')}")
        print(f"sample project ok in {sruns[0]['duration_ms']} ms", flush=True)

        # ── Explore: chart query compiled by the WASM module ──
        q = {"workflow": [{"type": "view", "query": [{"op": "aggregate", "groupBy": ["g"], "measures": [
            {"field": "x", "agg": "sum", "asFieldKey": "s"}]}]}]}
        r = call("/api/explore/query", {"source": {"kind": "frame", "project_id": pid, "node_id": "gen"}, "payload": q})
        if len(r["rows"]) != 3:
            fail(f"Explore query returned {r['rows']}")
        print("explore ok", flush=True)
    except SystemExit:
        proc.kill()
        # A hang dumps every thread's stack each SFS_STACK_DUMP_SECONDS; show
        # the whole of the last dump (the stuck thread can be anywhere in it).
        text = "".join(stderr)
        start = text.rfind("Timeout (")
        print("backend stderr (last stack dump):\n" + text[start:] if start >= 0
              else "backend stderr (tail):\n" + "".join(stderr[-40:]), flush=True)
        raise

    # ── shutdown when the shell goes away ──
    proc.stdin.close()
    try:
        code = proc.wait(30)
    except subprocess.TimeoutExpired:
        proc.kill()
        fail("backend did not exit after stdin closed")
    if code != 0:
        fail(f"backend exited with {code}")
    print(f"PASS in {time.monotonic() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
