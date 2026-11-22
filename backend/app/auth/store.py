"""Sessions, API tokens and stored password hashes, in the metadata database.

Shared by every provider: the community password provider and the
multi-user providers in sfs-enterprise create sessions and tokens for their
own principals through these functions.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC

from sqlalchemy import delete, select

from app.auth.hashing import digest, new_secret
from app.core.database import AsyncSessionLocal
from app.models.auth import ApiToken, AuthSecret, AuthSession

SESSION_MAX_DAYS = 30            # a session ends after this, however active
_TOUCH_EVERY = timedelta(minutes=1)
TOKEN_PREFIX = "sfs_"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def idle_timeout() -> timedelta:
    from app.core import app_settings
    try:
        hours = float(app_settings.get("SFS_SESSION_IDLE_HOURS") or 12)
    except (TypeError, ValueError):
        hours = 12
    return timedelta(hours=max(hours, 0.1))


# ── secrets (password hashes) ────────────────────────────────────────────────

async def get_secret(name: str) -> str | None:
    async with AsyncSessionLocal() as db:
        row = await db.get(AuthSecret, name)
        return row.value_hash if row else None


async def set_secret(name: str, value_hash: str) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(AuthSecret, name)
        if row:
            row.value_hash, row.updated_at = value_hash, _now()
        else:
            db.add(AuthSecret(name=name, value_hash=value_hash))
        await db.commit()


# ── sessions ─────────────────────────────────────────────────────────────────

async def create_session(principal_id: str, user_agent: str | None = None) -> tuple[str, str]:
    """A new session. Returns (the cookie value, its hash)."""
    raw = new_secret()
    h = digest(raw)
    async with AsyncSessionLocal() as db:
        db.add(AuthSession(id_hash=h, principal_id=principal_id, user_agent=(user_agent or "")[:255],
                           expires_at=_now() + timedelta(days=SESSION_MAX_DAYS)))
        await db.commit()
    return raw, h


async def lookup_session(raw: str, touch: bool = True) -> AuthSession | None:
    """The live session for a cookie value; expired or idle ones are removed.
    touch=False checks without counting as activity (WebSocket keepalives)."""
    if not raw:
        return None
    h, now = digest(raw), _now()
    async with AsyncSessionLocal() as db:
        row = await db.get(AuthSession, h)
        if row is None:
            return None
        if now >= row.expires_at or now - row.last_seen_at >= idle_timeout():
            await db.delete(row)
            await db.commit()
            return None
        if touch and now - row.last_seen_at >= _TOUCH_EVERY:
            row.last_seen_at = now
            await db.commit()
        return row


async def end_session(session_hash: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuthSession).where(AuthSession.id_hash == session_hash))
        await db.commit()


async def end_sessions(principal_id: str | None = None, keep: str | None = None) -> int:
    """End every session (of one principal), except `keep`. Returns how many."""
    q = delete(AuthSession)
    if principal_id is not None:
        q = q.where(AuthSession.principal_id == principal_id)
    if keep:
        q = q.where(AuthSession.id_hash != keep)
    async with AsyncSessionLocal() as db:
        n = (await db.execute(q)).rowcount
        await db.commit()
    return n or 0


# ── API tokens ───────────────────────────────────────────────────────────────

def _token_view(t: ApiToken) -> dict:
    iso = lambda d: d.isoformat() + "Z" if d else None          # noqa: E731
    return {"id": t.id, "name": t.name, "prefix": t.prefix, "created_at": iso(t.created_at),
            "last_used_at": iso(t.last_used_at), "expires_at": iso(t.expires_at)}


async def create_token(name: str, principal_id: str, expires_days: int | None = None) -> tuple[str, dict]:
    """A new API token. Returns (the token, shown once; its listing)."""
    raw = new_secret(TOKEN_PREFIX)
    t = ApiToken(id=str(uuid.uuid4()), name=name.strip()[:255] or "token", token_hash=digest(raw),
                 prefix=raw[:len(TOKEN_PREFIX) + 4], principal_id=principal_id,
                 expires_at=_now() + timedelta(days=expires_days) if expires_days else None)
    async with AsyncSessionLocal() as db:
        db.add(t)
        await db.commit()
    return raw, _token_view(t)


async def list_tokens(principal_id: str | None = None) -> list[dict]:
    q = select(ApiToken).order_by(ApiToken.created_at)
    if principal_id is not None:
        q = q.where(ApiToken.principal_id == principal_id)
    async with AsyncSessionLocal() as db:
        return [_token_view(t) for t in (await db.execute(q)).scalars()]


async def revoke_token(token_id: str, principal_id: str | None = None) -> bool:
    q = delete(ApiToken).where(ApiToken.id == token_id)
    if principal_id is not None:
        q = q.where(ApiToken.principal_id == principal_id)
    async with AsyncSessionLocal() as db:
        n = (await db.execute(q)).rowcount
        await db.commit()
    return bool(n)


async def lookup_token(raw: str) -> ApiToken | None:
    if not raw.startswith(TOKEN_PREFIX):
        return None
    now = _now()
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(ApiToken).where(ApiToken.token_hash == digest(raw)))).scalar_one_or_none()
        if t is None or (t.expires_at and now >= t.expires_at):
            return None
        if t.last_used_at is None or now - t.last_used_at >= _TOUCH_EVERY:
            t.last_used_at = now
            await db.commit()
        return t
