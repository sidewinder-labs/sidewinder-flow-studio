"""Signing in and out, the instance password, and API tokens.

/status, /login, /setup and /logout are public; the rest need auth.manage.
What a provider doesn't support it refuses with a message (AuthError).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import ratelimit, store
from app.auth.guard import clear_session_cookie, principal_of, set_session_cookie
from app.auth.permissions import CATALOGUE, public
from app.auth.principal import Principal
from app.auth.providers import SESSION_COOKIE, AuthError, get_provider

router = APIRouter()


def _refused(e: AuthError) -> JSONResponse:
    return JSONResponse({"detail": e.message}, status_code=e.status)


async def _signed_in(request: Request, principal: Principal) -> JSONResponse:
    raw, _ = await store.create_session(principal.id, request.headers.get("user-agent"))
    resp = JSONResponse({"ok": True, "user": principal.public()})
    set_session_cookie(resp, request, raw)
    return resp


def _throttled(request: Request) -> JSONResponse | None:
    wait = ratelimit.wait_seconds(ratelimit.client_key(request))
    if wait:
        return JSONResponse({"detail": f"Too many attempts. Try again in {wait} s."}, status_code=429,
                            headers={"Retry-After": str(wait)})
    return None


@router.get("/status")
@public
async def status(request: Request):
    provider = get_provider()
    principal = await provider.authenticate(request)
    perms = sorted(p for p in CATALOGUE if principal and provider.authorize(principal, p))
    return {"provider": provider.name, "sign_in_required": provider.sign_in_required,
            "signed_in": principal is not None, "user": principal.public() if principal else None,
            "permissions": perms, **(await provider.status())}


@router.post("/login")
@public
async def login(request: Request, payload: dict):
    if (r := _throttled(request)):
        return r
    key = ratelimit.client_key(request)
    try:
        principal = await get_provider().login(request, payload)
    except AuthError as e:
        if e.status == 401:
            ratelimit.failed(key)
        return _refused(e)
    ratelimit.succeeded(key)
    return await _signed_in(request, principal)


@router.post("/setup")
@public
async def setup(request: Request, payload: dict):
    if (r := _throttled(request)):
        return r
    try:
        principal = await get_provider().setup(request, payload)
    except AuthError as e:
        if e.status == 401:
            ratelimit.failed(ratelimit.client_key(request))
        return _refused(e)
    return await _signed_in(request, principal)


@router.post("/logout")
@public
async def logout(request: Request):
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        from app.auth.hashing import digest
        await store.end_session(digest(cookie))
    resp = JSONResponse({"ok": True})
    clear_session_cookie(resp, request)
    return resp


class PasswordChange(BaseModel):
    current: str
    new: str


@router.post("/password")
async def change_password(request: Request, body: PasswordChange):
    if (r := _throttled(request)):
        return r
    principal = principal_of(request)
    try:
        await get_provider().change_password(principal, body.current, body.new)
    except AuthError as e:
        if e.status == 401:
            ratelimit.failed(ratelimit.client_key(request))
        return _refused(e)
    return {"ok": True}


def _token_owner(request: Request) -> str:
    principal = principal_of(request)
    if principal is None or not get_provider().sign_in_required:
        raise HTTPException(400, "Sign-in is off on this installation, so API tokens aren't needed.")
    return principal.id


class TokenCreate(BaseModel):
    name: str
    expires_days: int | None = None


@router.get("/tokens")
async def list_tokens(request: Request):
    return await store.list_tokens(_token_owner(request))


@router.post("/tokens", status_code=201)
async def create_token(request: Request, body: TokenCreate):
    if body.expires_days is not None and body.expires_days < 1:
        raise HTTPException(400, "A token lasts at least one day; leave it blank for no expiry.")
    raw, view = await store.create_token(body.name, _token_owner(request), body.expires_days)
    return {**view, "token": raw}


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(request: Request, token_id: str):
    if not await store.revoke_token(token_id, _token_owner(request)):
        raise HTTPException(404, "No such token.")
    return Response(status_code=204)
