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
    assert {g["group"] for g in v["settings"]} == {"Memory & performance", "Explore", "Failure alerts"}
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
    t.join(10)
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
    assert a.status == "error" and a.error == "Stopped: over budget." and b.status == "ok"
    assert not (data / memory_guard.STOP_FILE).exists()
