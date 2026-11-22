"""Enforcement: every API route, the WebSocket, and cross-site requests.

- guard(area): the dependency each API router is mounted with. Signs the
  request in through the provider and checks the route's permission.
- authorize_ws(websocket): the same for the run-event WebSocket.
- OriginCheck: refuses state-changing requests and WebSockets that a browser
  sends from another site (CSRF, and cross-site WebSocket hijacking, which
  CORS doesn't cover). Requests without an Origin header (scripts, curl,
  tests) pass; the session cookie is also SameSite=Strict.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocket

from app.auth.permissions import CATALOGUE, WS_PERMISSION, permission_for
from app.auth.principal import Principal
from app.auth.providers import SESSION_COOKIE, get_provider
from app.auth.store import SESSION_MAX_DAYS

GUARD_ATTR = "__sfs_guard__"


def guard(area: str):
    async def check(request: Request) -> None:
        perm = permission_for(area, request.method, request.scope.get("endpoint"))
        if perm is None:
            return
        provider = get_provider()
        principal = await provider.authenticate(request)
        if principal is None:
            raise HTTPException(401, "Sign in to SFS.", headers={"WWW-Authenticate": 'Bearer realm="sfs"'})
        if not provider.authorize(principal, perm):
            raise HTTPException(403, f"You don't have permission to do this ({CATALOGUE[perm].lower()}).")
        request.state.principal = principal
    setattr(check, GUARD_ATTR, area)
    check.__name__ = f"guard_{area}"
    return check


async def authorize_ws(websocket: WebSocket) -> Principal | None:
    """The principal for a WebSocket, or None: then close it with 4401/4403."""
    provider = get_provider()
    principal = await provider.authenticate(websocket)
    if principal is None or not provider.authorize(principal, WS_PERMISSION):
        return None
    return principal


def principal_of(request: Request) -> Principal | None:
    return getattr(request.state, "principal", None)


# ── the session cookie ───────────────────────────────────────────────────────

def _https(conn: HTTPConnection) -> bool:
    return conn.url.scheme == "https" or conn.headers.get("x-forwarded-proto", "").lower() == "https"


def set_session_cookie(response: Response, conn: HTTPConnection, value: str) -> None:
    response.set_cookie(SESSION_COOKIE, value, max_age=SESSION_MAX_DAYS * 86400, path="/",
                        httponly=True, samesite="strict", secure=_https(conn))


def clear_session_cookie(response: Response, conn: HTTPConnection) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=_https(conn))


# ── cross-site requests ──────────────────────────────────────────────────────

_SAFE = {"GET", "HEAD", "OPTIONS"}


def origin_allowed(origin: str, host: str | None, forwarded_host: str | None) -> bool:
    if not origin or origin == "null":
        return False
    from app.core.config import settings
    if origin.rstrip("/") in {o.rstrip("/") for o in settings.CORS_ORIGINS}:
        return True
    netloc = urlparse(origin).netloc.lower()
    return bool(netloc) and netloc in {(host or "").lower(), (forwarded_host or "").lower()}


class OriginCheck:
    """ASGI middleware (it must see WebSockets, which HTTP middleware doesn't)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind == "websocket" or (kind == "http" and scope.get("method") not in _SAFE):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            origin = headers.get("origin")
            if origin is not None and not origin_allowed(origin, headers.get("host"), headers.get("x-forwarded-host")):
                if kind == "websocket":
                    await receive()                                   # websocket.connect
                    await send({"type": "websocket.close", "code": 4403})
                    return
                body = (b'{"detail":"This request came from another site, so SFS refused it. '
                        b'If you open SFS through another address, add it to CORS_ORIGINS."}')
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
