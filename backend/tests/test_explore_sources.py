"""Explore Phase 1: cached sources (frame, persist), listing, guardrails, endpoints."""
import json
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import explore as explore_api
from app.core import frame_cache, report_store
from app.core.config import settings
from app.core.database import get_db
from app.explore import compiler, sources

PID, NID = "proj-1", "node-1"
COUNT_BY_G = {"workflow": [{"type": "view", "query": [{"op": "aggregate", "groupBy": ["g"], "measures": [
    {"field": "x", "agg": "sum", "asFieldKey": "s"}]}]}]}
RAW = {"workflow": [{"type": "view", "query": [{"op": "raw", "fields": ["x"]}]}]}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated project, frame cache and persist.duckdb holding the same small table."""
    for k, v in {"FRAME_CACHE_DIR": "cache", "PROJECTS_DIR": "projects",
                 "PERSIST_DB": "reports/persist.duckdb"}.items():
        monkeypatch.setattr(settings, k, str(tmp_path / v))
    tbl = pa.table({"g": ["a", "a", "b"] * 10, "x": list(range(30))})
    frame_cache.store_frame_arrow(PID, NID, tbl)
    report_store.persist_frame("Daily Totals", tbl)
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / f"{PID}.json").write_text(json.dumps({
        "id": PID, "name": "Demo", "flows": [{"id": "f1", "name": "Flow 1", "nodes": [
            {"id": NID, "type": "processor",
             "data": {"label": "Load sales", "persist": {"table_name": "daily_totals"}}},
            {"id": "never-ran", "type": "processor", "data": {"label": "Not run"}},
            {"id": "trg", "type": "trigger", "data": {"label": "Start"}}]}]}))
    frame_cache.store_frame_arrow(PID, "trg", pa.table({}))
    return tbl


SOURCES = [("frame", {"project_id": PID, "node_id": NID}), ("persist", {"table": "daily_totals"})]


@pytest.mark.parametrize("kind,src", SOURCES)
def test_schema_and_query(store, kind, src):
    sch = sources.cached_schema(kind, **src)
    assert sch["rows"] == 30
    assert {f["fid"]: f["semanticType"] for f in sch["fields"]} == {"g": "nominal", "x": "quantitative"}
    r = sources.cached_query(kind, COUNT_BY_G, **src)
    assert {row["g"]: row["s"] for row in r.rows} == {"a": sum(i for i in range(30) if i % 3 != 2),
                                                      "b": sum(range(2, 30, 3))}
    assert not r.truncated and r.sql.startswith("SELECT")


def test_persist_is_attached_read_only(store):
    con = sources._cached_con("persist", table="daily_totals")
    try:
        with pytest.raises(duckdb.Error):
            con.execute("DROP TABLE sfs_persist.daily_totals")
    finally:
        con.close()
    # Connection closed: a writer can open the file again straight away.
    report_store.persist_frame("daily_totals", store)


@pytest.mark.parametrize("kind,src,missing", [
    ("frame", {"project_id": PID, "node_id": "nope"}, "has no output yet"),
    ("persist", {"table": "nope"}, "No persisted table 'nope'")])
def test_missing_source(store, kind, src, missing):
    with pytest.raises(FileNotFoundError, match=missing):
        sources.cached_schema(kind, **src)


def test_row_cap_truncates(store):
    r = sources.cached_query("frame", RAW, cap=7, project_id=PID, node_id=NID)
    assert len(r.rows) == 7 and r.truncated


def test_row_cap_not_flagged_at_exact_size(store):
    r = sources.cached_query("frame", RAW, cap=30, project_id=PID, node_id=NID)
    assert len(r.rows) == 30 and not r.truncated


def test_timeout_interrupts_query():
    con = duckdb.connect()
    slow = "SELECT count(*) FROM range(100000000) a, range(100000000) b WHERE a.range * b.range = -1"
    with pytest.raises(sources.QueryTimeout, match="stopped after 0.3 s"):
        sources._run_interruptible(con, slow, 0.3)
    assert con.execute("SELECT 42").fetchone() == (42,)   # connection still usable


def test_list_cached(store):
    out = sources.list_cached()
    assert [(f["label"], f["rows"], f["columns"]) for f in out["frames"]] == [("Load sales", 30, 2)]
    assert out["frames"][0]["project"] == "Demo" and out["frames"][0]["flow"] == "Flow 1"
    assert out["frames"][0]["column_names"] == ["g", "x"]       # zero-column trigger frame skipped
    assert [(p["table"], p["rows"], p["node"]) for p in out["persisted"]] == \
        [("daily_totals", 30, "Demo › Load sales")]


def test_frame_meta_cache_follows_file_changes(store):
    path = sources.Path(settings.FRAME_CACHE_DIR) / PID / f"{NID}.arrow"
    assert sources._frame_meta(path)["rows"] == 30
    frame_cache.store_frame_arrow(PID, NID, pa.table({"g": ["a"], "x": [1]}))
    assert sources._frame_meta(path)["rows"] == 1


def test_list_cached_without_persist_db(store, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PERSIST_DB", str(tmp_path / "missing.duckdb"))
    assert sources.list_cached()["persisted"] == []


def test_bin_count_makes_n_bins():
    con = duckdb.connect()
    con.execute("CREATE TABLE sfs_src AS SELECT i * 1.0 AS x FROM range(1000) t(i)")
    p = {"workflow": [
        {"type": "transform", "transform": [{"key": "b", "expression": {
            "op": "binCount", "as": "b", "params": [{"type": "field", "value": "x"}], "num": 10}}]},
        {"type": "view", "query": [{"op": "aggregate", "groupBy": ["b"], "measures": [
            {"field": "*", "agg": "count", "asFieldKey": "n"}]}]}]}
    rows = dict(con.execute(compiler.compile(p, [{"fid": "x", "semanticType": "quantitative"}])).fetchall())
    assert rows == {float(i): 100 for i in range(1, 11)}


# ── endpoints ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(store, monkeypatch):
    app = FastAPI()
    app.include_router(explore_api.router, prefix="/api/explore")

    async def no_db():
        yield None
    app.dependency_overrides[get_db] = no_db

    async def conns(_db):
        return [SimpleNamespace(name="pg", dialect="postgresql", password_enc="secret")]
    monkeypatch.setattr(explore_api, "list_connections", conns)
    return TestClient(app)


def test_sources_endpoint_lists_without_credentials(client):
    d = client.get("/api/explore/sources").json()
    assert len(d["frames"]) == 1 and len(d["persisted"]) == 1
    assert [{k: c[k] for k in ("name", "dialect")} for c in d["connections"]] == [{"name": "pg", "dialect": "postgresql"}]
    assert set(d["connections"][0]) == {"name", "dialect", "browser"}            # no host, user or secrets


def test_schema_endpoint_persist(client):
    d = client.get("/api/explore/schema", params={"kind": "persist", "table": "daily_totals"}).json()
    assert d["rows"] == 30 and [f["fid"] for f in d["fields"]] == ["g", "x"]


def test_schema_endpoint_db_error_is_400(client, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("Code: 516. Authentication failed\nstack…")
    monkeypatch.setattr(sources, "cached_schema", broken)
    r = client.get("/api/explore/schema", params={"kind": "persist", "table": "daily_totals"})
    assert r.status_code == 400 and r.json()["detail"] == "Could not read schema: Code: 516. Authentication failed"


def test_query_endpoint(client):
    r = client.post("/api/explore/query", json={
        "source": {"kind": "frame", "project_id": PID, "node_id": NID}, "payload": COUNT_BY_G})
    d = r.json()
    assert r.status_code == 200 and len(d["rows"]) == 2
    assert d["truncated"] is False and d["row_cap"] == settings.EXPLORE_ROW_CAP and "sql" in d


def test_query_endpoint_errors(client, monkeypatch):
    post = lambda src: client.post("/api/explore/query", json={"source": src, "payload": COUNT_BY_G})
    assert post({"kind": "persist", "table": "nope"}).status_code == 404
    assert post({"kind": "bogus"}).status_code == 400

    def slow(*a, **k):
        raise sources.QueryTimeout("Query stopped after 30 s — filter or aggregate further")
    monkeypatch.setattr(sources, "cached_query", slow)
    r = post({"kind": "frame", "project_id": PID, "node_id": NID})
    assert r.status_code == 408 and "stopped after 30 s" in r.json()["detail"]
