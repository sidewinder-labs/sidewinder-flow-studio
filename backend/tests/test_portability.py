"""Portability: deterministic project files, flow/project export and import,
secret connection options encrypted at rest, connection export/import, and
Explore's table browser (PostgreSQL partitions, search)."""
import asyncio
import json
import os
import uuid

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import connections as conn_api
from app.api import explore as explore_api
from app.api import projects as projects_api
from app.core import database
from app.core.config import settings
from app.services import connection_service as cs
from app.services import portability, project_service as ps


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path / "frames"))
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'meta.db'}"
    engine = create_async_engine(db_url)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def init():
        from app.models import connection, variable  # noqa: F401
        async with engine.begin() as c:
            await c.run_sync(database.Base.metadata.create_all)
    asyncio.run(init())

    async def get_db():
        async with Session() as s:
            yield s

    app = FastAPI(version="9.9")
    app.include_router(projects_api.router, prefix="/api/projects")
    app.include_router(conn_api.router, prefix="/api/connections")
    app.include_router(explore_api.router, prefix="/api/explore")
    app.dependency_overrides[database.get_db] = get_db
    client = TestClient(app)
    client.db_url = f"sqlite:///{tmp_path / 'meta.db'}"
    client.raw_db = lambda sql: sa.create_engine(client.db_url).connect().execute(sa.text(sql)).fetchall()
    return client


def _flow(name="Load", conn="warehouse"):
    return {
        "id": "f1", "name": name, "active": True, "imports": "import pandas as pd\n",
        "shared_functions": f'def eng():\n    return get_engine("{conn}")\n',
        "next_run": "2026-01-01T00:00:00",
        "nodes": [
            {"id": "t", "type": "trigger", "position": {"x": 80.4, "y": 200.6}, "selected": True,
             "dragging": False, "width": 150, "height": 40, "positionAbsolute": {"x": 80.4, "y": 200.6},
             "data": {"label": "Start", "run_mode": "schedule", "cron": "0 * * * *", "timezone": "UTC"}},
            {"id": "r", "type": "processor", "position": {"x": 300, "y": 200},
             "data": {"label": "Read", "engine": "fast_read", "connection": conn, "mode": "table", "table": "t"}},
            {"id": "p", "type": "processor", "position": {"x": 500.5, "y": 200},
             "data": {"label": "Write", "engine": "pandas",
                      "code": f'df = df\nfast_write(df, "{conn}", "out")\n',
                      "persist": {"target": conn, "table_name": "out"}, "source_node": "r"}},
        ],
        "edges": [{"id": "e2", "source": "r", "target": "p", "sourceHandle": "out", "targetHandle": "in", "selected": True},
                  {"id": "e1", "source": "t", "target": "r", "sourceHandle": "out", "targetHandle": "in"}],
    }


# ── 1. Deterministic serialization ───────────────────────────────────────────

def test_saving_twice_is_byte_identical_and_drops_canvas_state(env):
    from app.engine.registry import EXAMPLES, load_plugins
    load_plugins()
    flows = [_flow()] + [{**e["flow"], "id": str(uuid.uuid4()), "name": e["name"]} for e in EXAMPLES]
    pid = env.post("/api/projects/", json={"name": "Det"}).json()["id"]
    path = ps._path(pid)
    assert env.put(f"/api/projects/{pid}", json={"flows": flows}).status_code == 200
    first = path.read_bytes()
    loaded = env.get(f"/api/projects/{pid}").json()
    env.put(f"/api/projects/{pid}", json={"flows": loaded["flows"]})      # the app's round trip
    env.put(f"/api/projects/{pid}", json={"flows": loaded["flows"]})
    assert path.read_bytes() == first                                      # updated_at untouched too
    assert first.endswith(b"\n")

    stored = json.loads(first)
    f = stored["flows"][0]
    assert "next_run" not in f
    assert [n["id"] for n in f["nodes"]] == ["p", "r", "t"] and [e["id"] for e in f["edges"]] == ["e1", "e2"]
    t = next(n for n in f["nodes"] if n["id"] == "t")
    assert t["position"] == {"x": 80, "y": 201}
    assert not {"selected", "dragging", "width", "height", "positionAbsolute"} & set(t)
    assert list(stored) == sorted(stored)
    assert 'return get_engine(\\"warehouse\\")' in first.decode()           # code stays one readable string


def test_a_real_change_still_saves(env):
    pid = env.post("/api/projects/", json={"name": "Det"}).json()["id"]
    before = env.get(f"/api/projects/{pid}").json()
    flows = before["flows"]
    flows[0]["name"] = "Renamed"
    after = env.put(f"/api/projects/{pid}", json={"flows": flows}).json()
    assert after["flows"][0]["name"] == "Renamed" and after["updated_at"] != before["updated_at"]


# ── 2. Secret options encrypted at rest ──────────────────────────────────────

def test_secret_keys_definition():
    from app.services.secret_keys import is_secret
    for k in ("password", "PASSWD", "sas_token", "client_secret", "private_key", "api_key", "credentials_path", "TOKEN"):
        assert is_secret(k), k
    for k in ("warehouse", "role", "http_path", "protocol", "tenant_id", "client_id", "driver"):
        assert not is_secret(k), k


def test_secret_extras_are_encrypted_hidden_and_kept_on_edit(env):
    c = env.post("/api/connections/", json={
        "name": "lake", "dialect": "adls", "host": "acct", "password": "acctkey",
        "extra_params": {"sas_token": "sv=SECRET", "tenant_id": "t1", "client_secret": "CS"}}).json()
    raw = env.raw_db("SELECT extra_params, password_enc FROM connections")[0]
    assert "SECRET" not in raw[0] and "CS" not in raw[0] and '"tenant_id": "t1"' in raw[0]
    assert "acctkey" not in (raw[1] or "")
    listed = env.get("/api/connections/").json()[0]
    assert listed["extra_params"] == {"tenant_id": "t1"}
    assert listed["secret_extras"] == ["client_secret", "sas_token"] and listed["has_password"]
    assert "SECRET" not in json.dumps(listed) and "acctkey" not in json.dumps(listed)

    # The form sends only what it shows: secrets not sent are kept; a sent one replaces; cleared ones go.
    env.put(f"/api/connections/{c['id']}", json={"extra_params": {"tenant_id": "t2", "sas_token": "sv=NEW"},
                                                  "clear_secrets": ["client_secret"]})

    async def opts():
        async for db in env.app.dependency_overrides[database.get_db]():
            conn = await cs.get_connection(db, c["id"])
            return cs.get_storage_options(conn)
    o = asyncio.run(opts())
    assert o["sas_token"] == "sv=NEW" and o["tenant_id"] == "t2" and "client_secret" not in o
    assert o["account_key"] == "acctkey"


def test_migration_encrypts_existing_plaintext_extras(env):
    eng = sa.create_engine(env.db_url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO connections (id, name, dialect, extra_params, created_at) VALUES "
                          "('x', 'old', 'snowflake', :e, '2026-01-01')"), {"e": json.dumps({"warehouse": "WH", "private_key": "PK"})})
        assert cs.encrypt_plaintext_extras(c) == 1
        assert cs.encrypt_plaintext_extras(c) == 0                         # idempotent
        stored = c.execute(sa.text("SELECT extra_params FROM connections WHERE id='x'")).scalar()
    assert "PK" not in stored and json.loads(stored)["warehouse"] == "WH"

    class Row:
        extra_params, password_enc = stored, None
    assert cs.read_extras(Row()) == {"warehouse": "WH", "private_key": "PK"}


def test_added_columns_reach_an_old_database(tmp_path):
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE connections (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255), "
                          "dialect VARCHAR(64), host VARCHAR(255), port INTEGER, database VARCHAR(255), "
                          "username VARCHAR(255), password_enc TEXT, extra_params TEXT, created_at DATETIME)")
        from app.models import connection  # noqa: F401
        database._add_missing_columns(c)
        assert "options" in {col["name"] for col in sa.inspect(c).get_columns("connections")}


# ── 3. Flow and project export / import ──────────────────────────────────────

def _project(env, flows):
    pid = env.post("/api/projects/", json={"name": "Source", "description": "d"}).json()["id"]
    env.put(f"/api/projects/{pid}", json={"flows": flows})
    return pid


def test_export_holds_the_flow_and_a_manifest_but_no_credentials(env):
    env.post("/api/connections/", json={"name": "warehouse", "dialect": "postgresql", "host": "h",
                                        "username": "u", "password": "PW"})
    from app.services import custom_processor_service as cps
    cps.create_custom({"name": "Passthrough", "code": 'df = df\nfast_write(df, "warehouse", "out")\n'})
    pid = _project(env, [_flow()])
    b = env.get(f"/api/projects/{pid}/export").json()
    assert b["format"] == "sfs-export" and b["format_version"] == 1 and b["sfs_version"] == "9.9"
    assert b["kind"] == "project" and b["name"] == "Source" and len(b["flows"]) == 1
    assert b["manifest"]["connections"] == ["warehouse"]
    assert b["manifest"]["custom_processors"] == ["Passthrough"] and b["custom_processors"][0]["code"]
    assert "PW" not in json.dumps(b) and "next_run" not in json.dumps(b)
    assert env.get(f"/api/projects/{pid}/export").content == env.get(f"/api/projects/{pid}/export").content
    one = env.get(f"/api/projects/{pid}/export", params={"flow_id": "f1"}).json()
    assert one["kind"] == "flow" and one["name"] == "Load"


def test_import_regenerates_ids_imports_inactive_and_maps_connections(env, tmp_path, monkeypatch):
    pid = _project(env, [_flow()])
    bundle = env.get(f"/api/projects/{pid}/export").json()

    # A fresh instance: no projects, one connection under another name
    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "other" / "projects"))
    env.post("/api/connections/", json={"name": "dwh_prod", "dialect": "postgresql", "host": "h"})
    pre = env.post("/api/projects/import/preview", json={"bundle": bundle}).json()
    assert pre["missing_connections"] == ["warehouse"] and pre["local_connections"] == ["dwh_prod"]
    assert pre["flows"] == ["Load"] and pre["missing_engines"] == []

    p = env.post("/api/projects/import", json={"bundle": bundle, "connection_map": {"warehouse": "dwh_prod"}}).json()
    assert p["id"] != pid and p["name"] == "Source"
    f = p["flows"][0]
    old_ids = {"f1", "t", "r", "p", "e1", "e2"}
    assert f["id"] not in old_ids and not {n["id"] for n in f["nodes"]} & old_ids
    assert not {e["id"] for e in f["edges"]} & old_ids
    ids = {n["data"]["label"]: n["id"] for n in f["nodes"]}
    assert {(e["source"], e["target"]) for e in f["edges"]} == {(ids["Start"], ids["Read"]), (ids["Read"], ids["Write"])}
    write = next(n for n in f["nodes"] if n["data"]["label"] == "Write")["data"]
    assert write["source_node"] == ids["Read"]                            # a node named by id follows it
    assert f["active"] is False                                           # schedules import inactive
    read = next(n for n in f["nodes"] if n["data"]["label"] == "Read")["data"]
    assert read["connection"] == "dwh_prod" and write["persist"]["target"] == "dwh_prod"
    assert 'fast_write(df, "dwh_prod", "out")' in write["code"]
    assert 'get_engine("dwh_prod")' in f["shared_functions"]
    assert f["nodes"][0]["position"] == {k: round(v) for k, v in f["nodes"][0]["position"].items()}

    # Unresolved: names stay, so the node shows the usual "connection not found" error
    q = env.post("/api/projects/import", json={"bundle": bundle}).json()
    assert 'get_engine("warehouse")' in q["flows"][0]["shared_functions"]

    # As flows of an existing project: a second "Load" gets a distinct name
    into = env.post("/api/projects/import", json={"bundle": bundle, "project_id": p["id"]}).json()
    assert [x["name"] for x in into["flows"]] == ["Load", "Load (2)"]


def test_newer_or_foreign_files_are_rejected_clearly(env):
    pid = _project(env, [_flow()])
    b = env.get(f"/api/projects/{pid}/export").json()
    r = env.post("/api/projects/import", json={"bundle": {**b, "format_version": 2, "sfs_version": "2.0"}})
    assert r.status_code == 400 and "newer SFS (2.0" in r.json()["detail"]
    assert env.post("/api/projects/import", json={"bundle": {"flows": []}}).status_code == 400
    assert env.post("/api/projects/import", json={"bundle": {**b, "format_version": "x"}}).status_code == 400


def test_missing_plugin_engine_is_reported_and_kept(env):
    f = _flow()
    f["nodes"][2]["data"]["engine"] = "sklearn_fancy"
    pid = _project(env, [f])
    b = env.get(f"/api/projects/{pid}/export").json()
    assert b["manifest"]["engines"] == [{"name": "sklearn_fancy", "plugin": None, "version": None}]
    pre = env.post("/api/projects/import/preview", json={"bundle": b}).json()
    assert [e["name"] for e in pre["missing_engines"]] == ["sklearn_fancy"]
    p = env.post("/api/projects/import", json={"bundle": b}).json()
    assert any(n["data"].get("engine") == "sklearn_fancy" for n in p["flows"][0]["nodes"])


# ── 4. Connection and variable export / import ───────────────────────────────

def test_connection_export_strips_secrets_and_import_needs_credentials(env):
    env.post("/api/connections/", json={"name": "lake", "dialect": "adls", "host": "acct", "password": "K",
                                        "extra_params": {"sas_token": "SAS", "tenant_id": "t"},
                                        "browser": {"show_views": False}})
    env.post("/api/connections/", json={"name": "lite", "dialect": "sqlite", "database": "/x.db"})
    from app.api import metadata as meta_api
    env.app.include_router(meta_api.router, prefix="/api/metadata")
    env.post("/api/metadata/variables", json={"key": "REGION", "value": "eu"})
    b = env.get("/api/connections/export").json()
    text = json.dumps(b)
    assert "SAS" not in text and '"K"' not in text and "sas_token" in text   # the name, so import can ask for it
    lake = next(c for c in b["connections"] if c["name"] == "lake")
    assert lake["extra_params"] == {"tenant_id": "t"} and lake["browser"]["show_views"] is False
    assert lake["requires"] == {"password": True, "extras": ["sas_token"]}
    assert b["variables"] == [{"scope": "global", "key": "REGION", "value": "eu", "description": None}]

    # Import on a second instance: delete everything here first
    for c in env.get("/api/connections/").json():
        env.delete(f"/api/connections/{c['id']}")
    pre = env.post("/api/connections/import/preview", json={"bundle": b}).json()
    assert {c["name"]: c["exists"] for c in pre["connections"]} == {"lake": False, "lite": False}
    assert pre["variables_existing"] == ["REGION"]
    r = env.post("/api/connections/import", json={"bundle": b}).json()
    assert sorted(r["created"]) == ["lake", "lite"] and r["variables_skipped"] == 1
    got = {c["name"]: c for c in env.get("/api/connections/").json()}
    assert got["lake"]["needs_credentials"] and got["lake"]["needs"] == {"password": True, "extras": ["sas_token"]}
    assert not got["lite"]["needs_credentials"]
    assert got["lake"]["browser"]["show_views"] is False
    t = env.post(f"/api/connections/{got['lake']['id']}/test").json()
    assert not t["ok"] and "imported without its password and sas_token" in t["error"]

    # Entering the credentials clears the state
    env.put(f"/api/connections/{got['lake']['id']}", json={"password": "K2", "extra_params": {"tenant_id": "t", "sas_token": "S2"}})
    assert not env.get("/api/connections/").json()[0]["needs_credentials"]


def test_connection_import_collisions_skip_overwrite_rename(env):
    env.post("/api/connections/", json={"name": "wh", "dialect": "postgresql", "host": "old", "password": "KEEP",
                                        "extra_params": {"sslrootcert_key": "SECRETFILE"}})
    b = {"format": "sfs-connections", "format_version": 1, "connections": [
        {"name": "wh", "dialect": "postgresql", "host": "new", "extra_params": {"sslmode": "require"},
         "requires": {"password": True, "extras": ["sslrootcert_key"]}}]}
    assert env.post("/api/connections/import", json={"bundle": b}).json()["skipped"] == ["wh"]
    assert env.get("/api/connections/").json()[0]["host"] == "old"

    r = env.post("/api/connections/import", json={"bundle": b, "on_conflict": {"wh": "rename:wh_copy"}}).json()
    assert r["renamed"] == {"wh": "wh_copy"}
    assert {c["name"] for c in env.get("/api/connections/").json()} == {"wh", "wh_copy"}

    r = env.post("/api/connections/import", json={"bundle": b, "on_conflict": {"wh": "overwrite"}}).json()
    wh = next(c for c in env.get("/api/connections/").json() if c["name"] == "wh")
    assert r["overwritten"] == ["wh"] and wh["host"] == "new" and wh["extra_params"] == {"sslmode": "require"}
    assert wh["has_password"] and wh["secret_extras"] == ["sslrootcert_key"] and not wh["needs_credentials"]


# ── 5. Explore browser ───────────────────────────────────────────────────────

def test_sqlite_browser_search_and_view_toggle(env, tmp_path):
    db = tmp_path / "shop.db"
    with sa.create_engine(f"sqlite:///{db}").begin() as c:
        c.exec_driver_sql("CREATE TABLE orders (id INT)")
        c.exec_driver_sql("CREATE TABLE customers (id INT)")
        c.exec_driver_sql("CREATE VIEW big_orders AS SELECT * FROM orders")
    env.post("/api/connections/", json={"name": "shop", "dialect": "sqlite", "database": str(db)})
    sch = env.get("/api/explore/schemas", params={"connection": "shop"}).json()
    assert [s["schema"] for s in sch["schemas"]] == ["main"]
    names = lambda r: [(t["name"], t["kind"]) for t in r["tables"]]  # noqa: E731
    got = env.get("/api/explore/tables", params={"connection": "shop", "schema": "main"}).json()
    assert names(got) == [("customers", "table"), ("orders", "table"), ("big_orders", "view")]
    found = env.get("/api/explore/tables", params={"connection": "shop", "q": "ORD"}).json()
    assert names(found) == [("orders", "table"), ("big_orders", "view")]
    env.put("/api/explore/browser-settings", json={"connection": "shop", "settings": {"show_views": False}})
    got = env.get("/api/explore/tables", params={"connection": "shop", "schema": "main"}).json()
    assert names(got) == [("customers", "table"), ("orders", "table")]


PG = os.environ.get("SFS_TEST_PG_URL")


@pytest.mark.skipif(not PG, reason="set SFS_TEST_PG_URL to run against PostgreSQL")
def test_postgres_partitions_hidden_parent_shown_and_search_across_schemas():
    from app.explore import catalog
    eng = sa.create_engine(PG)
    a, b = f"sfs_t_{uuid.uuid4().hex[:6]}", f"sfs_u_{uuid.uuid4().hex[:6]}"
    with eng.begin() as c:
        c.exec_driver_sql(f"CREATE SCHEMA {a}; CREATE SCHEMA {b}")
        c.exec_driver_sql(f"CREATE TABLE {a}.events (id int, day date) PARTITION BY RANGE (day)")
        for m in range(1, 13):
            c.exec_driver_sql(f"CREATE TABLE {a}.events_2026_{m:02d} PARTITION OF {a}.events "
                              f"FOR VALUES FROM ('2026-{m:02d}-01') TO ('{2026 + (m == 12)}-{m % 12 + 1:02d}-01')")
        c.exec_driver_sql(f"CREATE TABLE {a}.base (id int); CREATE TABLE {a}.child () INHERITS ({a}.base)")
        c.exec_driver_sql(f"CREATE VIEW {a}.v_events AS SELECT * FROM {a}.events")
        c.exec_driver_sql(f"CREATE MATERIALIZED VIEW {a}.mv_events AS SELECT * FROM {a}.events")
        c.exec_driver_sql(f"CREATE TABLE {b}.event_log (id int)")
    try:
        d = {**cs.BROWSER_DEFAULTS}
        got = catalog.objects(eng, "postgres", d, schema=a)["tables"]
        assert [(t["name"], t["kind"], t["partitioned"]) for t in got] == [
            ("base", "table", False), ("events", "table", True),
            ("mv_events", "materialized_view", False), ("v_events", "view", False)]
        shown = catalog.objects(eng, "postgres", {**d, "show_partitions": True}, schema=a)["tables"]
        assert sum(t["kind"] == "partition" for t in shown) == 13            # 12 partitions + the inheritance child
        no_views = catalog.objects(eng, "postgres", {**d, "show_views": False}, schema=a)["tables"]
        assert [t["name"] for t in no_views] == ["base", "events"]
        found = catalog.objects(eng, "postgres", d, q="EVENT")["tables"]
        assert {(t["schema"], t["name"]) for t in found} >= {(a, "events"), (a, "v_events"), (a, "mv_events"), (b, "event_log")}
        assert not any(t["kind"] == "partition" for t in found)
        sch = {s["schema"]: s["count"] for s in catalog.schemas(eng, "postgres", d)}
        assert sch[a] == 4 and sch[b] == 1 and "pg_catalog" not in sch and "information_schema" not in sch
        only = catalog.schemas(eng, "postgres", {**d, "include_schemas": [b]})
        assert [s["schema"] for s in only] == [b]
    finally:
        with eng.begin() as c:
            c.exec_driver_sql(f"DROP SCHEMA {a} CASCADE; DROP SCHEMA {b} CASCADE")
