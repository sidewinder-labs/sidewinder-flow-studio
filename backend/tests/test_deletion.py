"""Deleting projects and flows: schedules, cached frames, persisted tables,
the trash, and refusal while a flow runs."""
import json

import pyarrow as pa
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import projects as projects_api
from app.core import frame_cache, report_store
from app.core.config import settings
from app.engine.active_runs import tracking
from app.scheduler import scheduler


def _flow(fid, *, cron=None, active=True, nodes=()):
    trigger = {"id": f"{fid}-start", "type": "trigger", "position": {"x": 0, "y": 0},
               "data": {"label": "Start", "run_mode": "schedule" if cron else "manual",
                        "cron": cron or "", "timezone": "UTC"}}
    return {"id": fid, "name": fid, "active": active, "nodes": [trigger, *nodes], "edges": []}


def _node(nid, persist_to=None):
    data = {"label": nid, "engine": "pandas", "code": ""}
    if persist_to:
        data["persist"] = {"enabled": True, "target": "local", "table_name": persist_to}
    return {"id": nid, "type": "processor", "position": {"x": 0, "y": 0}, "data": data}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path / "frames"))
    monkeypatch.setattr(settings, "PERSIST_DB", str(tmp_path / "persist.duckdb"))
    for job in scheduler._scheduler.get_jobs():
        job.remove()
    app = FastAPI()
    app.include_router(projects_api.router, prefix="/api/projects")
    yield TestClient(app)
    for job in scheduler._scheduler.get_jobs():
        job.remove()


def _project(client, flows):
    pid = client.post("/api/projects/", json={"name": "Doomed"}).json()["id"]
    assert client.put(f"/api/projects/{pid}", json={"flows": flows}).status_code == 200
    return pid


def _frame(pid, node_id):
    frame_cache.store_frame_arrow(pid, node_id, pa.table({"x": [1, 2]}))


def test_delete_project_unschedules_every_flow_and_removes_frames(env):
    pid = _project(env, [_flow("a", cron="0 * * * *", nodes=[_node("n1")]),
                         _flow("b", cron="5 * * * *", nodes=[_node("n2")])])
    keep = _project(env, [_flow("c", cron="0 1 * * *")])
    assert scheduler.scheduled_flow_ids() == {"a", "b", "c"}
    _frame(pid, "n1"); _frame(pid, "n2")

    preview = env.get(f"/api/projects/{pid}/delete-preview").json()
    assert {f["id"]: f["scheduled"] for f in preview["flows"]} == {"a": True, "b": True}

    assert env.delete(f"/api/projects/{pid}").status_code == 200
    assert scheduler.scheduled_flow_ids() == {"c"}                 # only the other project's
    assert not (frame_cache.Path(settings.FRAME_CACHE_DIR) / pid).exists()
    assert [p["id"] for p in env.get("/api/projects/").json()] == [keep]
    assert env.get(f"/api/projects/{pid}").status_code == 404


def test_removing_a_flow_on_save_unschedules_only_that_flow(env):
    a, b = _flow("a", cron="0 * * * *", nodes=[_node("n1")]), _flow("b", cron="5 * * * *", nodes=[_node("n2")])
    pid = _project(env, [a, b])
    _frame(pid, "n1"); _frame(pid, "n2")

    assert env.put(f"/api/projects/{pid}", json={"flows": [b]}).status_code == 200
    assert scheduler.scheduled_flow_ids() == {"b"}
    assert not frame_cache.has_frame(pid, "n1") and frame_cache.has_frame(pid, "n2")


def test_a_frame_shared_with_a_remaining_flow_is_kept(env):
    a, copy = _flow("a", nodes=[_node("shared")]), _flow("copy", nodes=[_node("shared")])
    pid = _project(env, [a, copy])
    _frame(pid, "shared")
    env.put(f"/api/projects/{pid}", json={"flows": [copy]})
    assert frame_cache.has_frame(pid, "shared")


def test_persisted_tables_are_dropped_only_when_asked(env):
    report_store.persist_frame("mine", pa.table({"x": [1]}))
    report_store.persist_frame("shared", pa.table({"x": [1]}))
    pid = _project(env, [_flow("a", nodes=[_node("n1", "mine"), _node("n2", "shared")])])
    _project(env, [_flow("other", nodes=[_node("n3", "shared")])])    # also writes "shared"

    # "shared" is offered for neither: another project still persists to it
    assert env.get(f"/api/projects/{pid}/delete-preview").json()["persisted_tables"] == ["mine"]

    assert env.delete(f"/api/projects/{pid}").json()["dropped_tables"] == []
    assert report_store.has_persist("mine")
    env.post(f"/api/projects/trash/{pid}/restore")

    assert env.delete(f"/api/projects/{pid}?drop_persisted=true").json()["dropped_tables"] == ["mine"]
    assert not report_store.has_persist("mine") and report_store.has_persist("shared")


def test_flow_delete_drops_its_tables_when_asked(env):
    report_store.persist_frame("t_a", pa.table({"x": [1]}))
    a, b = _flow("a", nodes=[_node("n1", "t_a")]), _flow("b")
    pid = _project(env, [a, b])
    assert env.get(f"/api/projects/{pid}/delete-preview?flow_id=a").json()["persisted_tables"] == ["t_a"]
    env.put(f"/api/projects/{pid}", json={"flows": [b], "drop_persisted": True})
    assert not report_store.has_persist("t_a")


def test_trash_restore_brings_flows_back_inactive(env):
    pid = _project(env, [_flow("a", cron="0 * * * *")])
    env.delete(f"/api/projects/{pid}")
    trash = env.get("/api/projects/trash/").json()
    assert [t["id"] for t in trash] == [pid] and trash[0]["deleted_at"] and trash[0]["flows"] == 1

    restored = env.post(f"/api/projects/trash/{pid}/restore").json()
    assert [f["active"] for f in restored["flows"]] == [False]
    assert scheduler.scheduled_flow_ids() == set()                # nothing fires until re-activated
    assert env.get("/api/projects/trash/").json() == []
    assert env.get(f"/api/projects/{pid}").status_code == 200

    env.delete(f"/api/projects/{pid}")
    assert env.delete(f"/api/projects/trash/{pid}").status_code == 204
    assert env.get("/api/projects/trash/").json() == []
    assert env.post(f"/api/projects/trash/{pid}/restore").status_code == 404


def test_deleting_while_running_is_refused(env):
    a, b = _flow("a"), _flow("b")
    pid = _project(env, [a, b])
    with tracking(pid, "a"):
        assert env.get(f"/api/projects/{pid}/delete-preview").json()["running"] is True
        r = env.delete(f"/api/projects/{pid}")
        assert r.status_code == 409 and "running" in r.json()["detail"]
        r = env.put(f"/api/projects/{pid}", json={"flows": [b]})     # removes the running flow
        assert r.status_code == 409
        assert env.put(f"/api/projects/{pid}", json={"flows": [a]}).status_code == 200   # removes b: fine
    assert env.delete(f"/api/projects/{pid}").status_code == 200


def test_schedules_are_loaded_at_startup(env):
    pid = _project(env, [_flow("a", cron="0 * * * *"), _flow("off", cron="0 * * * *", active=False)])
    for job in scheduler._scheduler.get_jobs():
        job.remove()                                              # as after a restart
    scheduler.load_all_schedules()
    assert scheduler.scheduled_flow_ids() == {"a"}
    assert json.loads((frame_cache.Path(settings.PROJECTS_DIR) / f"{pid}.json").read_text())


def test_trashed_projects_are_not_listed_or_scheduled_at_startup(env):
    pid = _project(env, [_flow("a", cron="0 * * * *")])
    env.delete(f"/api/projects/{pid}")
    scheduler.load_all_schedules()
    assert scheduler.scheduled_flow_ids() == set()
    assert env.get("/api/projects/").json() == []
