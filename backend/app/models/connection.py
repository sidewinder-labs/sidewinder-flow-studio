from sqlalchemy import String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, UTC
from app.core.database import Base


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    dialect: Mapped[str] = mapped_column(String(64), nullable=False)
    host: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    database: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    password_enc: Mapped[str | None] = mapped_column(Text)
    extra_params: Mapped[str | None] = mapped_column(Text)      # JSON; secret values encrypted
    # JSON, non-secret: {"browser": {...Explore settings}, "needs": {"password": bool, "extras": [names]}}
    options: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
