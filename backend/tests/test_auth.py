"""Sign-in: the instance password, sessions, API tokens, the setup code, rate
limiting, cross-site requests, the WebSocket, the recovery command, and the
provider interface a plugin (sfs-enterprise) uses for roles."""
import asyncio
import json
from datetime import datetime, timedelta, UTC

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from app.api import auth as auth_api, projects as projects_api, ws as ws_api
from app.auth import providers, ratelimit, store
from app.auth.guard import OriginCheck, guard
from app.auth.hashing import hash_password, verify_password
from app.auth.principal import Principal
from app.core import database
from app.core.config import settings

PW = "correct horse battery"


@pytest.fixture
def make(tmp_path, monkeypatch):
    """make(mode='password', env_password=None) → a signed-out TestClient."""
    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "projects"))
    # No pool: TestClient runs each request and WebSocket on a fresh event loop,
    # and a pooled aiosqlite connection would call back into a loop already closed
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'meta.db'}", poolclass=NullPool)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(store, "AsyncSessionLocal", Session)

    async def init():
        from app.models import auth, connection, variable  # noqa: F401
        async with engine.begin() as c:
            await c.run_sync(database.Base.metadata.create_all)
    asyncio.run(init())

    async def get_db():
        async with Session() as s:
            yield s

    def _make(mode="password", env_password=None, provider=None):
        monkeypatch.setenv("SFS_AUTH", mode)
        if env_password:
            monkeypatch.setenv("SFS_PASSWORD", env_password)
        else:
            monkeypatch.delenv("SFS_PASSWORD", raising=False)
        providers.reset_for_tests()
        ratelimit.reset()
        if provider is not None:
            providers.set_provider(provider)
        asyncio.run(providers.get_provider().startup())
        app = FastAPI()
        app.add_middleware(OriginCheck)
        app.include_router(projects_api.router, prefix="/api/projects", dependencies=[Depends(guard("projects"))])
        app.include_router(auth_api.router, prefix="/api/auth", dependencies=[Depends(guard("auth"))])
        app.include_router(ws_api.router, prefix="/ws")
        app.dependency_overrides[database.get_db] = get_db
        c = TestClient(app)
        c.provider = providers.get_provider()
        c.session_maker = Session
        return c
    yield _make
    providers.reset_for_tests()
    ratelimit.reset()


def _set_up(c):
    r = c.post("/api/auth/setup", json={"code": c.provider._setup_code, "password": PW})
    assert r.status_code == 200, r.text
    return r


# ── the password ─────────────────────────────────────────────────────────────

def test_hashes_are_salted_scrypt_and_verify():
    a, b = hash_password(PW), hash_password(PW)
    assert a.startswith("scrypt$") and a != b and PW not in a
    assert verify_password(PW, a) and not verify_password("wrong", a) and not verify_password(PW, "garbage")


def test_nothing_is_reachable_before_a_password_is_set(make, capsys):
    c = make()
    assert c.provider._setup_code in capsys.readouterr().err            # printed to the log
    s = c.get("/api/auth/status").json()
    assert s["needs_setup"] and not s["signed_in"] and s["permissions"] == []
    assert c.get("/api/projects/").status_code == 401
    assert c.post("/api/auth/login", json={"password": PW}).status_code == 409


def test_setup_needs_the_code_and_a_real_password(make):
    c = make()
    assert c.post("/api/auth/setup", json={"code": "WRNG-CODE", "password": PW}).status_code == 401
    r = c.post("/api/auth/setup", json={"code": c.provider._setup_code, "password": "short"})
    assert r.status_code == 400 and "8 characters" in r.json()["detail"]
    _set_up(c)
    assert c.get("/api/projects/").status_code == 200                     # setting it signs you in
    again = c.post("/api/auth/setup", json={"code": "anything", "password": PW})
    assert again.status_code == 409                                       # once only


def test_sign_in_sign_out_and_the_cookie(make):
    c = make()
    r = _set_up(c)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get("/api/projects/").status_code == 401
    assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"password": PW}).status_code == 200
    assert c.get("/api/auth/status").json()["signed_in"]


def test_a_stolen_cookie_stops_working_after_sign_out(make):
    c = make()
    _set_up(c)
    stolen = dict(c.cookies)
    c.post("/api/auth/logout")
    other = TestClient(c.app, cookies=stolen)
    assert other.get("/api/projects/").status_code == 401


def test_idle_sessions_expire(make, monkeypatch):
    c = make()
    _set_up(c)
    monkeypatch.setattr(store, "idle_timeout", lambda: timedelta(seconds=-1))
    assert c.get("/api/projects/").status_code == 401


def test_password_from_the_environment(make):
    c = make(env_password=PW)
    s = c.get("/api/auth/status").json()
    assert not s["needs_setup"] and s["password_from_env"]
    assert c.post("/api/auth/login", json={"password": PW}).status_code == 200
    r = c.post("/api/auth/password", json={"current": PW, "new": "another long one"})
    assert r.status_code == 409 and "SFS_PASSWORD" in r.json()["detail"]


def test_a_weak_environment_password_is_refused_not_used(make):
    c = make(env_password="short")
    assert c.get("/api/auth/status").json()["needs_setup"]
    assert c.post("/api/auth/login", json={"password": "short"}).status_code == 409


def test_changing_the_password_signs_other_browsers_out(make):
    c = make()
    _set_up(c)
    other = TestClient(c.app)
    assert other.post("/api/auth/login", json={"password": PW}).status_code == 200
    assert c.post("/api/auth/password", json={"current": "wrong", "new": "x" * 12}).status_code == 401
    assert c.post("/api/auth/password", json={"current": PW, "new": "a new password"}).status_code == 200
    assert c.get("/api/projects/").status_code == 200                     # this browser stays in
    assert other.get("/api/projects/").status_code == 401
    assert other.post("/api/auth/login", json={"password": "a new password"}).status_code == 200


# ── guessing ─────────────────────────────────────────────────────────────────

def test_repeated_wrong_passwords_are_slowed_down(make):
    c = make()
    _set_up(c)
    c.cookies.clear()
    codes = [c.post("/api/auth/login", json={"password": f"guess{i}"}).status_code for i in range(ratelimit.FREE_TRIES + 1)]
    assert codes[:ratelimit.FREE_TRIES] == [401] * ratelimit.FREE_TRIES
    r = c.post("/api/auth/login", json={"password": PW})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1     # even the right one waits
    assert "Try again" in r.json()["detail"]


def test_many_addresses_at_once_pause_sign_in_for_all(make):
    c = make()
    _set_up(c)
    c.cookies.clear()
    for i in range(ratelimit.GLOBAL_FAILS_PER_MIN):
        c.post("/api/auth/login", json={"password": "x"}, headers={"X-Real-IP": f"10.0.{i // 250}.{i % 250}"})
    assert c.post("/api/auth/login", json={"password": PW}, headers={"X-Real-IP": "10.9.9.9"}).status_code == 429


# ── API tokens ───────────────────────────────────────────────────────────────

def test_api_tokens_for_scripts(make):
    c = make()
    _set_up(c)
    t = c.post("/api/auth/tokens", json={"name": "CI deploy"}).json()
    assert t["token"].startswith("sfs_") and t["prefix"] == t["token"][:8]
    listed = c.get("/api/auth/tokens").json()
    assert [x["name"] for x in listed] == ["CI deploy"] and "token" not in listed[0]
    script = TestClient(c.app)
    hdr = {"Authorization": f"Bearer {t['token']}"}
    assert script.get("/api/projects/", headers=hdr).status_code == 200
    assert script.get("/api/auth/status", headers=hdr).json()["user"]["kind"] == "token"
    assert script.get("/api/projects/", headers={"Authorization": "Bearer sfs_made_up"}).status_code == 401
    assert c.delete(f"/api/auth/tokens/{t['id']}").status_code == 204
    assert script.get("/api/projects/", headers=hdr).status_code == 401


def test_expired_tokens_are_refused(make):
    c = make()
    _set_up(c)
    t = c.post("/api/auth/tokens", json={"name": "short", "expires_days": 1}).json()

    async def age():
        from sqlalchemy import update
        from app.models.auth import ApiToken
        async with c.session_maker() as db:
            await db.execute(update(ApiToken).values(expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)))
            await db.commit()
    asyncio.run(age())
    assert TestClient(c.app).get("/api/projects/", headers={"Authorization": f"Bearer {t['token']}"}).status_code == 401


# ── cross-site requests and the WebSocket ────────────────────────────────────

def test_cross_site_changes_are_refused(make):
    c = make()
    _set_up(c)
    evil = c.post("/api/projects/", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and "another site" in evil.json()["detail"]
    assert c.post("/api/projects/", json={"name": "x"}, headers={"Origin": "http://testserver"}).status_code == 201
    assert c.post("/api/projects/", json={"name": "y"}, headers={"Origin": "null"}).status_code == 403
    # Vite's dev server rewrites Host; its origin is in CORS_ORIGINS
    assert c.post("/api/projects/", json={"name": "z"}, headers={"Origin": settings.CORS_ORIGINS[0]}).status_code == 201
    # behind a proxy that passes the original host on
    assert c.post("/api/projects/", json={"name": "w"},
                  headers={"Origin": "https://sfs.example.com", "X-Forwarded-Host": "sfs.example.com"}).status_code == 201


def test_cross_site_checks_apply_with_sign_in_off_too(make):
    """The desktop app has no sign-in, but a web page must not drive its API."""
    c = make(mode="none")
    assert c.get("/api/projects/").status_code == 200
    assert c.post("/api/projects/", json={"name": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_the_websocket_needs_sign_in_and_the_same_site(make):
    c = make()
    with c.websocket_connect("/ws/flow/f1") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401
    _set_up(c)
    with c.websocket_connect("/ws/flow/f1") as ws:
        ws.send_text("ping")
        assert json.loads(ws.receive_text()) == {"type": "pong"}
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/ws/flow/f1", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_text()
    assert e.value.code == 4403


def test_an_open_websocket_closes_once_its_session_ends(make):
    c = make()
    _set_up(c)
    with c.websocket_connect("/ws/flow/f1") as ws:
        ws.send_text("ping")
        assert json.loads(ws.receive_text()) == {"type": "pong"}
        TestClient(c.app, cookies=dict(c.cookies)).post("/api/auth/logout")     # another tab signs out
        ws.send_text("ping")
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401


def test_websocket_keepalives_dont_keep_a_session_alive(make, monkeypatch):
    c = make()
    _set_up(c)
    seen = []
    real = store.lookup_session

    async def spy(raw, touch=True):
        seen.append(touch)
        return await real(raw, touch)
    monkeypatch.setattr(store, "lookup_session", spy)
    with c.websocket_connect("/ws/flow/f1") as ws:
        ws.send_text("ping")
        ws.receive_text()
    c.get("/api/projects/")
    assert seen == [False, False, True]          # connect, ping: no touch; an API call: touch


# ── recovery ─────────────────────────────────────────────────────────────────

def test_set_password_command_resets_and_signs_everyone_out(make, monkeypatch, capsys):
    import io
    from app.auth.__main__ import _set_password
    c = make()
    _set_up(c)
    monkeypatch.setattr("sys.stdin", io.StringIO("recovered password\n"))
    assert asyncio.run(_set_password(from_stdin=True)) == 0
    assert "1 browser session(s) signed out" in capsys.readouterr().out
    assert c.get("/api/projects/").status_code == 401
    assert c.post("/api/auth/login", json={"password": "recovered password"}).status_code == 200


# ── sign-in off ──────────────────────────────────────────────────────────────

def test_sign_in_off_is_one_local_user(make):
    c = make(mode="none")
    s = c.get("/api/auth/status").json()
    assert not s["sign_in_required"] and s["signed_in"] and s["user"]["kind"] == "local"
    assert c.post("/api/auth/tokens", json={"name": "x"}).status_code == 400


def test_desktop_defaults_to_no_sign_in_and_servers_to_a_password(monkeypatch):
    monkeypatch.delenv("SFS_AUTH", raising=False)
    monkeypatch.setenv("SFS_DESKTOP", "1")
    assert providers.configured_mode() == "none"
    monkeypatch.delenv("SFS_DESKTOP")
    assert providers.configured_mode() == "password"
    monkeypatch.setenv("SFS_AUTH", "bogus")
    assert providers.configured_mode() == "password"                       # fails closed


# ── the interface sfs-enterprise builds on ───────────────────────────────────

class _ViewerProvider(providers.AuthProvider):
    """A provider with one signed-in viewer: may open projects, not change them."""
    name = "test-roles"

    async def authenticate(self, conn):
        return Principal(id="ann", name="Ann", permissions=frozenset({"projects.view", "app.view"}))


def test_a_plugin_provider_decides_permissions(make):
    c = make(provider=_ViewerProvider())
    assert c.get("/api/projects/").status_code == 200
    r = c.post("/api/projects/", json={"name": "x"})
    assert r.status_code == 403 and "change and delete projects" in r.json()["detail"]
    s = c.get("/api/auth/status").json()
    assert s["provider"] == "test-roles" and s["permissions"] == ["app.view", "projects.view"]


def test_plugins_register_providers_through_the_registry_api(make):
    from app.engine.registry import RegistryAPI
    make()
    RegistryAPI(source="test").register_auth_provider(_ViewerProvider())
    assert providers.get_provider().name == "test-roles"
    with pytest.raises(TypeError):
        RegistryAPI(source="test").register_auth_provider(object())
