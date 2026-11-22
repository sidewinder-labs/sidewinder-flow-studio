"""Meta → Settings (app.core.app_settings), the memory measurements and the
memory budget guard, and closing runs a previous process left "running"."""
import asyncio
import json
import os
import threading
import time

import pandas as pd
import polars as pl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import app_settings, database, memory, memory_guard, restart
from app.core.config import settings
from app.engine import active_runs, executor


@pytest.fixture
def data(tmp_path, monkeypatch):
    """An empty data folder, no environment overrides, and os.environ /
    settings restored afterwards."""
    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(app_settings, "_ENV", {})
    monkeypatch.setattr(app_settings, "_AT_START", {})
    for d in app_settings.DEFS:
        if d.key in app_settings._FIELDS:
            monkeypatch.setattr(settings, d.key, getattr(settings, d.key))
        else:
            monkeypatch.delenv(d.key, raising=False)
    return tmp_path


# ── settings ─────────────────────────────────────────────────────────────────

def test_defaults_then_saved_values_apply_at_once(data):
    assert app_settings.get("EXPLORE_ROW_CAP") == 50_000 and app_settings.source("EXPLORE_ROW_CAP") == "default"
    app_settings.update({"EXPLORE_ROW_CAP": "1000", "SFS_DUCKDB_MEMORY_LIMIT": "2GB", "SFS_PARALLEL_BRANCHES": True})
    assert settings.EXPLORE_ROW_CAP == 1000                       # read where it's used: no restart
    assert os.environ["SFS_DUCKDB_MEMORY_LIMIT"] == "2GB" and executor._parallel_enabled()
    assert app_settings.source("EXPLORE_ROW_CAP") == "app"
    app_settings.update({"EXPLORE_ROW_CAP": None, "SFS_DUCKDB_MEMORY_LIMIT": "", "SFS_PARALLEL_BRANCHES": False})
    assert settings.EXPLORE_ROW_CAP == 50_000 and "SFS_DUCKDB_MEMORY_LIMIT" not in os.environ
    assert not executor._parallel_enabled()
    assert json.loads((data / "settings.json").read_text()) == {}


def test_invalid_values_are_refused_and_nothing_is_saved(data):
    with pytest.raises(ValueError) as e:
        app_settings.update({"EXPLORE_ROW_CAP": "10", "SFS_MEMORY_BUDGET": "5%", "SFS_DUCKDB_MEMORY_LIMIT": "lots",
                             "SMTP_HOST": "mail.example", "NOPE": 1})
    errs = e.value.args[0]
    assert set(errs) == {"EXPLORE_ROW_CAP", "SFS_MEMORY_BUDGET", "SFS_DUCKDB_MEMORY_LIMIT", "NOPE"}
    assert app_settings.get("SMTP_HOST") is None                  # the valid one wasn't saved either


@pytest.mark.parametrize("raw,ok", [("", True), ("off", True), ("60%", True), ("12GB", True), ("1.5 gb", True),
                                    ("100MB", False), ("99%", False), ("lots", False)])
def test_budget_values(data, raw, ok):
    if ok:
        app_settings.update({"SFS_MEMORY_BUDGET": raw})
    else:
        with pytest.raises(ValueError):
            app_settings.update({"SFS_MEMORY_BUDGET": raw})


def test_environment_wins_and_is_locked(data, monkeypatch):
    monkeypatch.setattr(app_settings, "_ENV", {"EXPLORE_ROW_CAP": "777"})
    assert app_settings.get("EXPLORE_ROW_CAP") == 777 and app_settings.source("EXPLORE_ROW_CAP") == "environment"
    with pytest.raises(ValueError, match="environment"):
        app_settings.update({"EXPLORE_ROW_CAP": "1000"})
    item = next(s for s in app_settings.view()["settings"] if s["key"] == "EXPLORE_ROW_CAP")
    assert item["locked"] and item["value"] == 777


def test_secrets_are_encrypted_and_never_returned(data):
    app_settings.update({"SMTP_PASSWORD": "hunter2-secret"})
    assert "hunter2" not in (data / "settings.json").read_text()
    assert app_settings.get("SMTP_PASSWORD") == "hunter2-secret" and settings.SMTP_PASSWORD == "hunter2-secret"
    item = next(s for s in app_settings.view()["settings"] if s["key"] == "SMTP_PASSWORD")
    assert item["is_set"] and "value" not in item and "hunter2" not in json.dumps(app_settings.view())


def test_restart_settings_wait_for_a_restart(data):
    app_settings.apply_all()
    assert app_settings.restart_needed() == []
    app_settings.update({"POLARS_MAX_THREADS": 4})
    assert app_settings.restart_needed() == ["Polars threads"]
    assert "POLARS_MAX_THREADS" not in os.environ                 # not applied in this process
    app_settings.update({"POLARS_MAX_THREADS": None})
    assert app_settings.restart_needed() == []                    # back to how it started


def test_settings_api(data, monkeypatch):
    from fastapi import FastAPI
    from app.api import settings as api
    app = FastAPI()
    app.include_router(api.router, prefix="/api/settings")
    c = TestClient(app)
    v = c.get("/api/settings/").json()
    assert {g["group"] for g in v["settings"]} == {"Memory & performance", "Explore", "Sign-in", "Failure alerts"}
    assert v["memory"]["total"] and v["memory"]["budget"]
    r = c.put("/api/settings/", json={"values": {"EXPLORE_ROW_CAP": "5"}})
    assert r.status_code == 400 and "EXPLORE_ROW_CAP" in r.json()["detail"]["errors"]
    v = c.put("/api/settings/", json={"values": {"EXPLORE_ROW_CAP": "5000"}}).json()
    assert next(s for s in v["settings"] if s["key"] == "EXPLORE_ROW_CAP")["value"] == 5000
    monkeypatch.setattr(restart, "can_restart", lambda: False)
    assert c.post("/api/settings/restart").status_code == 400


# ── memory ───────────────────────────────────────────────────────────────────

def test_sizes():
    assert memory.parse_size("512MB") == 512 << 20 and memory.parse_size("1.5 gb") == int(1.5 * (1 << 30))
    with pytest.raises(ValueError):
        memory.parse_size("12 bananas")
    assert memory.fmt_size(3 << 30) == "3.0 GB"


@pytest.mark.skipif(not os.path.exists("/proc/self/status"), reason="Linux")
def test_measures_this_process_and_the_machine():
    used, total = memory.process_bytes(), memory.total_bytes()
    assert 10 << 20 < used < total


def test_budget_bytes(data, monkeypatch):
    monkeypatch.setattr(memory, "total_bytes", lambda: 10 << 30)
    assert memory_guard.budget_bytes() == 8 << 30                 # blank: 80%
    for raw, want in (("50%", 5 << 30), ("2GB", 2 << 30), ("off", None)):
        app_settings.update({"SFS_MEMORY_BUDGET": raw})
        assert memory_guard.budget_bytes() == want
    app_settings.update({"SFS_MEMORY_BUDGET": "4GB"})
    assert memory_guard.duckdb_default_limit() == "2048MB"


# ── the guard ────────────────────────────────────────────────────────────────

@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setattr(memory_guard, "_exceeded", None)
    monkeypatch.setattr(restart, "can_restart", lambda: False)    # never exit the test process
    yield
    memory_guard._exceeded = None


def test_tripped_budget_stops_fast_write_between_batches(guard, tmp_path):
    fast_write = executor.build_exec_context({"db": f"sqlite:///{tmp_path / 'w.db'}"}, {},
                                             dialects={"db": "sqlite"})["fast_write"]
    memory_guard._trip(9 << 30, 8 << 30)
    with pytest.raises(memory_guard.MemoryBudgetExceeded, match="memory budget"):
        fast_write(pl.DataFrame({"a": range(100)}).lazy(), "db", "t", chunk_rows=10)


@pytest.mark.parametrize("streamed", [False, True], ids=["table", "streamed-to-cache"])
def test_tripped_budget_interrupts_a_running_duckdb_query(guard, tmp_path, streamed):
    out = {}
    def run():
        try:
            sql = ("SELECT a.range * b.range AS x" if streamed else "SELECT SUM(a.range * b.range)") + \
                  " FROM range(100000) a, range(100000) b"
            executor.exec_duckdb_processor(sql, [], output_path=str(tmp_path / "o.arrow") if streamed else None)
        except Exception as e:
            out["e"] = e
    t = threading.Thread(target=run)
    t.start()
    for _ in range(100):                                          # wait until the query has started
        if memory_guard._duckdb:
            break
        time.sleep(0.02)
    memory_guard._trip(9 << 30, 8 << 30)
    for _ in range(40):                                           # as the watcher does, every check
        t.join(0.25)
        if not t.is_alive():
            break
        memory_guard._interrupt_duckdb()
    assert not t.is_alive() and isinstance(out.get("e"), memory_guard.MemoryBudgetExceeded)
    assert not memory_guard._duckdb                               # unregistered


def test_watcher_trips_while_a_flow_runs_and_resets_after(guard, monkeypatch):
    monkeypatch.setattr(memory_guard, "budget_bytes", lambda: 1)  # anything is over budget
    monkeypatch.setattr(memory_guard, "hard_limit", lambda b: 1 << 50)
    monkeypatch.setattr(memory_guard, "_thread", None)
    memory_guard.start()
    with active_runs.tracking("p", "f"):
        for _ in range(40):
            if memory_guard.exceeded():
                break
            time.sleep(0.05)
        assert memory_guard.exceeded() and "memory budget" in memory_guard.exceeded()
    for _ in range(40):                                           # the run ended: reset
        if not memory_guard.exceeded():
            break
        time.sleep(0.1)
    assert memory_guard.exceeded() is None


def test_executor_stops_the_flow_with_the_reason(guard, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path / "frames"))
    async def quiet(*a, **k): pass
    monkeypatch.setattr(executor.ws_manager, "broadcast", quiet)
    memory_guard._trip(9 << 30, 8 << 30)
    flow = {"id": "f", "name": "f", "nodes": [
        {"id": "t", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"label": "Start", "run_mode": "manual"}},
        {"id": "n", "type": "processor", "position": {"x": 0, "y": 0}, "data": {"label": "n", "engine": "pandas", "code": "df = pd.DataFrame({'a': [1]})"}},
    ], "edges": [{"id": "e", "source": "t", "target": "n", "sourceHandle": "out", "targetHandle": "in"}]}
    by = {r["node_id"]: r for r in asyncio.run(executor.execute_flow("p", flow, {}, {}))}
    assert by["n"]["status"] == "error" and by["n"]["detail"].startswith("Stopped: SFS was using")
    assert "Traceback" not in by["n"]["detail"]


# ── runs left "running" by a previous process ────────────────────────────────

def test_interrupted_runs_are_closed_at_startup_with_the_reason(data, monkeypatch):
    from app.models.run_history import RunHistory
    eng = create_async_engine(f"sqlite+aiosqlite:///{data / 'meta.db'}")
    Session = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "AsyncSessionLocal", Session)

    async def go():
        async with eng.begin() as c:
            await c.run_sync(database.Base.metadata.create_all)
        async with Session() as s:
            s.add_all([RunHistory(id="a", project_id="p", flow_id="f", trigger="manual", status="running"),
                       RunHistory(id="b", project_id="p", flow_id="g", trigger="manual", status="ok")])
            await s.commit()
        (data / memory_guard.STOP_FILE).write_text(json.dumps({"message": "Stopped: over budget."}))
        assert await active_runs.close_interrupted_runs() == 1
        async with Session() as s:
            a, b = await s.get(RunHistory, "a"), await s.get(RunHistory, "b")
        await eng.dispose()
        return a, b
    a, b = asyncio.run(go())
    assert a.status == "crashed" and a.error == "Stopped: over budget." and b.status == "ok"
    assert not (data / memory_guard.STOP_FILE).exists()



# ── pandas nodes are checked before loading ──────────────────────────────────

def cache_frame(tmp_path, monkeypatch, rows=200_000):
    import numpy as np
    import pyarrow as pa
    from app.core import frame_cache
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path / "frames"))
    t = pa.table({"i": np.arange(rows), "x": np.random.rand(rows),
                  "s": pa.array(np.random.choice(["nexus4_1", "stand", "a"], rows)),
                  "n": pa.array([None if k % 7 == 0 else k for k in range(rows)], pa.int64())})
    frame_cache.store_frame_arrow("p", "src", t)
    return t


def test_pandas_estimate_matches_a_real_load(tmp_path, monkeypatch):
    from app.core import frame_cache
    t = cache_frame(tmp_path, monkeypatch)
    est = frame_cache.pandas_estimate("p", "src")
    real = frame_cache.load_frame("p", "src").memory_usage(deep=True, index=False).sum()
    assert est["rows"] == 200_000 and est["columns"] == 4
    assert abs(est["arrow_bytes"] - t.nbytes) <= t.nbytes * 0.05
    assert 0.8 * real <= est["pandas_bytes"] <= 1.25 * real
    assert est["load_bytes"] == est["arrow_bytes"] + est["pandas_bytes"]
    assert frame_cache.pandas_estimate("p", "nope") is None


def flow_with(engine):
    return {"id": "f", "name": "f", "nodes": [
        {"id": "src", "type": "processor", "position": {"x": 0, "y": 0}, "data": {"label": "src", "engine": "pandas"}},
        {"id": "n", "type": "processor", "position": {"x": 0, "y": 0},
         "data": {"label": "big", "engine": engine, "code": "df = df.head(1)" if engine == "pandas" else "df = df.head(1)"}},
    ], "edges": [{"id": "e", "source": "src", "target": "n", "sourceHandle": "out", "targetHandle": "in"}]}


def run_node_n(engine, monkeypatch):
    async def quiet(*a, **k): pass
    monkeypatch.setattr(executor.ws_manager, "broadcast", quiet)
    res = asyncio.run(executor.execute_flow("p", flow_with(engine), {}, {}, start_from_node="n"))
    return {r["node_id"]: r for r in res}["n"]


def test_a_pandas_node_too_big_for_memory_is_refused_before_loading(data, tmp_path, monkeypatch):
    from app.core import frame_cache
    cache_frame(tmp_path, monkeypatch)
    monkeypatch.setattr(memory_guard, "room", lambda: (1 << 20, "left in the memory budget"))
    loaded = []
    monkeypatch.setattr(executor, "load_frame", lambda *a: loaded.append(a) or frame_cache.load_frame(*a))
    r = run_node_n("pandas", monkeypatch)
    assert r["status"] == "error" and r["detail"].startswith("Not run: loading this node's input (200,000 rows)")
    assert "only 1.0 MB is left in the memory budget" in r["detail"] and "Polars or DuckDB" in r["detail"]
    assert "Traceback" not in r["detail"] and not loaded                  # nothing was loaded
    assert run_node_n("polars", monkeypatch)["status"] == "ok"           # Polars doesn't load it whole
    app_settings.update({"SFS_MEMORY_BUDGET": "off"})                    # the check goes with the budget
    assert run_node_n("pandas", monkeypatch)["status"] == "ok"


def test_a_pandas_node_that_fits_runs(data, tmp_path, monkeypatch):
    cache_frame(tmp_path, monkeypatch, rows=1000)
    assert run_node_n("pandas", monkeypatch)["status"] == "ok"


def test_estimate_endpoint(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from app.api import execute
    cache_frame(tmp_path, monkeypatch)
    app = FastAPI(); app.include_router(execute.router, prefix="/api/execute")
    v = TestClient(app).get("/api/execute/estimate/p", params={"node_ids": "src,missing"}).json()
    assert v["rows"] == 200_000 and len(v["inputs"]) == 1 and v["load_bytes"] > 0 and v["room"] > 0


def test_column_list_never_loads_the_whole_frame(tmp_path, monkeypatch):
    from app.api import metadata
    from app.core import frame_cache
    cache_frame(tmp_path, monkeypatch)
    monkeypatch.setattr(frame_cache, "load_frame", lambda *a: (_ for _ in ()).throw(AssertionError("loaded whole")))
    v = asyncio.run(metadata.frame_columns("p", "src"))
    assert v["ran"] and [c["name"] for c in v["columns"]] == ["i", "x", "s", "n"]


def test_view_as_python_gets_the_first_rows_when_inputs_dont_fit(tmp_path, monkeypatch):
    from app.api import capabilities
    from app.engine.registry import EngineSpec, ENGINES
    cache_frame(tmp_path, monkeypatch)
    seen = {}
    spec = EngineSpec(name="t_code", label="t", description="", run=lambda ec: None,
                      to_code=lambda params, inputs: seen.setdefault("rows", [len(f) for f in inputs]) and "df = df")
    monkeypatch.setitem(ENGINES, "t_code", spec)
    req = capabilities.ToCodeRequest(engine="t_code", node_data={}, project_id="p", parent_ids=["src"])
    asyncio.run(capabilities.to_code(req))
    assert seen["rows"] == [200_000]
    seen.clear(); monkeypatch.setattr(memory_guard, "room", lambda: (1 << 20, "free on this machine"))
    asyncio.run(capabilities.to_code(req))
    assert seen["rows"] == [10_000]


# ── crash reports ────────────────────────────────────────────────────────────

def test_running_nodes_are_tracked():
    with active_runs.tracking("p", "f"):
        active_runs.node_started("p", "f", "load")
        assert active_runs.snapshot() == [{"project_id": "p", "flow_id": "f", "nodes": ["load"]}]
        active_runs.node_finished("p", "f", "load")
        assert active_runs.snapshot() == [{"project_id": "p", "flow_id": "f", "nodes": []}]
    assert active_runs.snapshot() == []


def test_crash_reasons():
    gb = 1 << 30
    full = {"used": 29 * gb, "total": 31 * gb, "budget": None}
    r = active_runs.crash_reason(None, full, ["load"])
    assert r.startswith("Crashed while running node 'load': SFS was using 29.0 GB of 31.0 GB memory")
    assert "most likely ran out of memory" in r
    r = active_runs.crash_reason(None, {"used": 2 * gb, "total": 31 * gb, "budget": 25 * gb}, ["read"])
    assert r == "Crashed: SFS stopped while running node 'read' (restart, crash or the machine shutting down)."
    assert active_runs.crash_reason(None, None, []).startswith("Crashed: SFS stopped (restart")
    assert active_runs.crash_reason({"message": "Stopped: over budget."}, full, ["load"]) == \
        "Stopped: over budget. (node 'load')"


def test_heartbeat_names_the_running_nodes_and_goes_when_idle(data, guard, monkeypatch):
    monkeypatch.setattr(memory_guard, "_thread", None)
    memory_guard.start()
    beat = data / memory_guard.HEARTBEAT_FILE
    with active_runs.tracking("p", "f"):
        active_runs.node_started("p", "f", "load")
        for _ in range(40):
            if beat.exists():
                break
            time.sleep(0.1)
        b = json.loads(beat.read_text())
        assert b["runs"][0]["nodes"] == ["load"] and b["used"] > 0
        active_runs.node_finished("p", "f", "load")
    for _ in range(40):
        if not beat.exists():
            break
        time.sleep(0.1)
    assert not beat.exists()
