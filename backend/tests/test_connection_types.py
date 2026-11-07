"""Plugin connection types: fields from the registry, secret fields encrypted,
hidden from the API and exports, decrypted for engines at run time."""
import asyncio
import json

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import capabilities as cap_api
from app.api import connections as conn_api
from app.core import database
from app.core.config import settings
from app.engine import registry


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "meta.db"
    monkeypatch.setattr(settings, "METADATA_DB_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "projects"))
    registry.CONNECTION_TYPES.pop("acme", None)
    seen = {}

    def test(fields):
        seen.update(fields)
        return (fields["token"] == "T0K3N", "Connected" if fields["token"] == "T0K3N" else "bad token")
    registry.register_connection_type(dialect="acme", label="Acme Planning", test=test, fields=[
        {"key": "address", "label": "Address", "type": "string", "required": True},
        {"key": "port", "label": "Port", "type": "number", "default": 443},
        {"key": "ssl", "label": "SSL", "type": "boolean", "default": True},
        {"key": "mode", "label": "Mode", "type": "select", "options": ["a", "b"], "default": "a"},
        {"key": "token", "label": "Token", "type": "secret"},
        {"key": "client_pass_phrase", "label": "Phrase", "type": "secret"},
    ])
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def init():
        from app.models import connection, variable  # noqa: F401
        async with engine.begin() as c:
            await c.run_sync(database.Base.metadata.create_all)
    asyncio.run(init())

    async def get_db():
        async with Session() as s:
            yield s
    app = FastAPI()
    app.include_router(conn_api.router, prefix="/api/connections")
    app.include_router(cap_api.router, prefix="/api/capabilities")
    app.dependency_overrides[database.get_db] = get_db
    c = TestClient(app)
    c.raw = lambda q: sa.create_engine(f"sqlite:///{db}").connect().execute(sa.text(q)).fetchall()
    c.seen = seen
    yield c
    registry.CONNECTION_TYPES.pop("acme", None)
    from app.services import connection_service as cs
    cs._sync_engines.clear()


def _create(env, **extra):
    return env.post("/api/connections/", json={"name": "plan", "dialect": "acme", "extra_params": {
        "address": "tm1.example", "port": "8010", "ssl": False, "token": "T0K3N", "client_pass_phrase": "PP", **extra}}).json()


def test_type_is_listed_without_its_test_function(env):
    t = next(t for t in env.get("/api/capabilities/").json()["connection_types"] if t["dialect"] == "acme")
    assert t["label"] == "Acme Planning" and t["testable"] and "test" not in t
    assert [f["type"] for f in t["fields"]] == ["string", "number", "boolean", "select", "secret", "secret"]


def test_bad_field_type_and_dialect_rejected():
    with pytest.raises(ValueError, match="unknown type"):
        registry.register_connection_type(dialect="x1", label="X", fields=[{"key": "k", "type": "password"}])
    with pytest.raises(ValueError, match="lower-case"):
        registry.register_connection_type(dialect="Bad-Name", label="X", fields=[])


def test_secret_fields_encrypted_hidden_and_kept_on_edit(env):
    c = _create(env)
    raw = env.raw("SELECT extra_params FROM connections")[0][0]
    assert "T0K3N" not in raw and "PP" not in raw and '"address": "tm1.example"' in raw
    assert c["secret_extras"] == ["client_pass_phrase", "token"]             # 'pass_phrase' isn't a secret word: declared
    assert "T0K3N" not in json.dumps(c) and c["extra_params"]["address"] == "tm1.example"
    env.put(f"/api/connections/{c['id']}", json={"extra_params": {"address": "tm1b.example", "port": 9000, "ssl": True}})
    f = registry.RegistryAPI.get_connection("plan", "acme")
    assert f == {"name": "plan", "dialect": "acme", "address": "tm1b.example", "port": 9000, "ssl": True,
                 "mode": "a", "token": "T0K3N", "client_pass_phrase": "PP"}


def test_test_button_calls_the_plugin_with_decrypted_fields(env):
    c = _create(env)
    r = env.post(f"/api/connections/{c['id']}/test").json()
    assert r["ok"] and r["message"] == "Connected" and env.seen["port"] == 8010 and env.seen["ssl"] is False
    env.put(f"/api/connections/{c['id']}", json={"extra_params": {"address": "x", "token": "nope"}})
    r = env.post(f"/api/connections/{c['id']}/test").json()
    assert not r["ok"] and r["error"] == "bad token"


def test_get_connection_errors_name_the_problem(env):
    _create(env)
    with pytest.raises(ValueError, match="not found"):
        registry.RegistryAPI.get_connection("missing", "acme")
    with pytest.raises(ValueError, match="is a acme connection; this node needs"):
        registry.RegistryAPI.get_connection("plan", "powerbi")


def test_export_strips_secret_fields_and_import_needs_them(env):
    _create(env)
    b = env.get("/api/connections/export").json()
    assert "T0K3N" not in json.dumps(b) and "PP" not in json.dumps(b)
    assert b["connections"][0]["requires"] == {"password": False, "extras": ["client_pass_phrase", "token"]}
    env.delete(f"/api/connections/{env.get('/api/connections/').json()[0]['id']}")
    env.post("/api/connections/import", json={"bundle": b})
    got = env.get("/api/connections/").json()[0]
    assert got["needs_credentials"] and got["extra_params"]["address"] == "tm1.example"
    with pytest.raises(ValueError, match="imported without"):
        registry.RegistryAPI.get_connection("plan", "acme")


def test_get_engine_on_a_plugin_connection_explains(env):
    from app.services.connection_service import BROWSER_DEFAULTS, connection_maps  # noqa: F401
    from app.models.connection import Connection
    m = connection_maps([Connection(id="1", name="plan", dialect="acme", extra_params=None)])
    assert "not a SQL database" in m["urls"]["plan"] and "plan" not in m["broken"]
