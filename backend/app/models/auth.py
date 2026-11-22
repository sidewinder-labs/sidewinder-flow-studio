"""Sign-in state: the instance password, browser sessions and API tokens.

Sessions and tokens name a principal (`principal_id`), so a provider with
many users (sfs-enterprise) uses the same tables; the community password
provider has one principal, "instance".
Secrets are never stored: sessions and tokens by SHA-256, the password by
scrypt (app/auth/hashing.py).
"""
from datetime import datetime, UTC

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AuthSecret(Base):
    """Named secrets of the auth system; today one row, 'instance_password'."""
    __tablename__ = "auth_secrets"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_hash: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(255))


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)       # shown in lists: sfs_ab12…
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
