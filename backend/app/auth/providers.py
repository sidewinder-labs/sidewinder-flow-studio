"""The auth interface: one provider decides who a request comes from and what
it may do.

Community ships two:
- PasswordProvider (app/auth/password.py): one password protects the
  instance; the default for the server edition.
- NoAuthProvider: no sign-in, one local user. The desktop edition's default
  (it listens on 127.0.0.1 only), or any install with SFS_AUTH=none.

A plugin replaces them with `api.register_auth_provider(provider)` — this is
how sfs-enterprise adds user accounts, SSO and roles. Sessions, API tokens,
the session cookie, rate limiting and the route guard stay in core and are
shared by every provider.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from starlette.requests import HTTPConnection

from app.auth.principal import LOCAL, Principal

if TYPE_CHECKING:
    from fastapi import APIRouter

log = logging.getLogger("sfs.auth")

SESSION_COOKIE = "sfs_session"


class AuthError(Exception):
    """A sign-in or account action refused, with the status and the message
    the user sees."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class AuthProvider:
    """Base class. Override what the provider supports; the defaults refuse."""

    name = "base"
    sign_in_required = True

    async def startup(self) -> None:
        """Called once when SFS starts, after the database is ready."""

    async def authenticate(self, conn: HTTPConnection) -> Principal | None:
        """The principal behind a request or WebSocket, or None if not signed in."""
        return None

    def authorize(self, principal: Principal, permission: str) -> bool:
        return principal.can(permission)

    async def status(self) -> dict:
        """For the sign-in screen: {'needs_setup': bool, 'methods': [...], ...}."""
        return {"needs_setup": False, "methods": []}

    async def login(self, conn: HTTPConnection, payload: dict) -> Principal:
        raise AuthError(400, "This sign-in method isn't available.")

    async def setup(self, conn: HTTPConnection, payload: dict) -> Principal:
        raise AuthError(400, "Nothing to set up.")

    async def change_password(self, principal: Principal, current: str, new: str) -> None:
        raise AuthError(400, "Passwords aren't managed here.")

    def token_principal(self, token_name: str, principal_id: str) -> Principal | None:
        """The principal an API token acts as, or None to refuse it."""
        return None

    def routes(self) -> "APIRouter | None":
        """Extra endpoints, mounted at /api/auth/provider/ (e.g. an SSO callback)."""
        return None


class NoAuthProvider(AuthProvider):
    name = "none"
    sign_in_required = False

    async def authenticate(self, conn: HTTPConnection) -> Principal | None:
        return LOCAL


# ── which provider is in charge ──────────────────────────────────────────────

_plugin_provider: AuthProvider | None = None
_builtin: AuthProvider | None = None


def configured_mode() -> str:
    """SFS_AUTH: 'password' or 'none'. Unset: none on the desktop, password
    on a server. Anything else fails closed to password."""
    mode = (os.getenv("SFS_AUTH") or "").strip().lower()
    if not mode:
        return "none" if os.getenv("SFS_DESKTOP") == "1" else "password"
    if mode not in ("password", "none"):
        log.error("SFS_AUTH=%r is not 'password' or 'none': using password.", mode)
        return "password"
    return mode


def set_provider(provider: AuthProvider) -> None:
    """Replace the community providers (plugins: api.register_auth_provider)."""
    global _plugin_provider
    if not isinstance(provider, AuthProvider):
        raise TypeError("An auth provider must subclass app.auth.providers.AuthProvider")
    _plugin_provider = provider
    log.info("Sign-in handled by the '%s' provider", provider.name)


def get_provider() -> AuthProvider:
    global _builtin
    if _plugin_provider is not None:
        return _plugin_provider
    if _builtin is None or _builtin.name != configured_mode():
        if configured_mode() == "none":
            _builtin = NoAuthProvider()
        else:
            from app.auth.password import PasswordProvider
            _builtin = PasswordProvider()
    return _builtin


def reset_for_tests() -> None:
    global _plugin_provider, _builtin
    _plugin_provider = _builtin = None
