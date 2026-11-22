"""Community sign-in: one password protects the instance.

The password is set on first run, never defaulted:
- from SFS_PASSWORD (Docker, scripted installs) — it then can't be changed
  in the app, only by changing the variable; or
- in the browser, with a one-time setup code that SFS prints to its log at
  start, so whoever reaches the page first can't claim the instance; or
- on the server: `python -m app.auth set-password` (also the recovery path).

Anyone with the password may do everything, node code included: it decides
who gets in, not what they can do.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import sys

from starlette.requests import HTTPConnection

from app.auth import store
from app.auth.hashing import check_new_password, hash_password, verify_password
from app.auth.principal import Principal
from app.auth.providers import SESSION_COOKIE, AuthError, AuthProvider

log = logging.getLogger("sfs.auth")

SECRET_NAME = "instance_password"
PRINCIPAL_ID = "instance"


def _setup_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"            # no 0/O, 1/I
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


class PasswordProvider(AuthProvider):
    name = "password"

    def __init__(self):
        self._setup_code: str | None = None

    # ── lifecycle ──
    async def startup(self) -> None:
        env = os.getenv("SFS_PASSWORD")
        if env:
            try:
                check_new_password(env)
            except ValueError as e:
                log.error("SFS_PASSWORD is not used: %s Sign-in stays locked until it is fixed.", e)
                return
            stored = await store.get_secret(SECRET_NAME)
            if not stored or not verify_password(env, stored):
                await store.set_secret(SECRET_NAME, hash_password(env))
                await store.end_sessions(PRINCIPAL_ID)            # a new password signs everyone out
                log.info("Password set from SFS_PASSWORD.")
            return
        if await store.get_secret(SECRET_NAME) is None:
            self._setup_code = _setup_code()
            banner = (
                "\n" + "=" * 64 +
                "\n  SFS needs a password before anyone can use it."
                f"\n  Open SFS in the browser and enter this setup code: {self._setup_code}"
                "\n  (or set SFS_PASSWORD, or run: python -m app.auth set-password)"
                "\n" + "=" * 64 + "\n")
            print(banner, file=sys.stderr, flush=True)
            log.warning("Waiting for a password; setup code printed above.")

    @property
    def password_from_env(self) -> bool:
        return bool(os.getenv("SFS_PASSWORD"))

    # ── who is this ──
    async def authenticate(self, conn: HTTPConnection) -> Principal | None:
        auth = conn.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            t = await store.lookup_token(auth[7:].strip())
            return self.token_principal(t.name, t.principal_id) if t else None
        cookie = conn.cookies.get(SESSION_COOKIE)
        if cookie:
            # A WebSocket's keepalive re-checks the session but isn't activity
            s = await store.lookup_session(cookie, touch=conn.scope["type"] == "http")
            if s and s.principal_id == PRINCIPAL_ID:
                return Principal(id=PRINCIPAL_ID, name="SFS user", via="session", session_hash=s.id_hash)
        return None

    def token_principal(self, token_name: str, principal_id: str) -> Principal | None:
        if principal_id != PRINCIPAL_ID:
            return None
        return Principal(id=PRINCIPAL_ID, name=f"Token: {token_name}", kind="token", via="token")

    async def status(self) -> dict:
        return {"needs_setup": await store.get_secret(SECRET_NAME) is None,
                "password_from_env": self.password_from_env,
                "methods": [{"type": "password"}]}

    # ── signing in ──
    async def login(self, conn: HTTPConnection, payload: dict) -> Principal:
        stored = await store.get_secret(SECRET_NAME)
        if stored is None:
            raise AuthError(409, "No password is set yet. Enter the setup code from the SFS log.")
        if not verify_password(str(payload.get("password") or ""), stored):
            raise AuthError(401, "Wrong password.")
        return Principal(id=PRINCIPAL_ID, name="SFS user", via="session")

    async def setup(self, conn: HTTPConnection, payload: dict) -> Principal:
        if await store.get_secret(SECRET_NAME) is not None:
            raise AuthError(409, "A password is already set. Sign in with it.")
        code = str(payload.get("code") or "").strip().upper()
        if not self._setup_code or not hmac.compare_digest(code, self._setup_code):
            raise AuthError(401, "That setup code isn't right. It's printed in the SFS log at start "
                                 "(docker compose logs backend).")
        password = str(payload.get("password") or "")
        try:
            check_new_password(password)
        except ValueError as e:
            raise AuthError(400, str(e)) from None
        await store.set_secret(SECRET_NAME, hash_password(password))
        self._setup_code = None
        log.info("Password set in the browser.")
        return Principal(id=PRINCIPAL_ID, name="SFS user", via="session")

    async def change_password(self, principal: Principal, current: str, new: str) -> None:
        if self.password_from_env:
            raise AuthError(409, "The password comes from SFS_PASSWORD: change it there and restart SFS.")
        stored = await store.get_secret(SECRET_NAME)
        if stored is None or not verify_password(current, stored):
            raise AuthError(401, "The current password is wrong.")
        try:
            check_new_password(new)
        except ValueError as e:
            raise AuthError(400, str(e)) from None
        await store.set_secret(SECRET_NAME, hash_password(new))
        await store.end_sessions(PRINCIPAL_ID, keep=principal.session_hash)   # other browsers sign in again
