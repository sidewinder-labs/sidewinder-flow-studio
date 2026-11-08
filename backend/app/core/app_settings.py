"""Settings edited in the app (Meta → Settings), so nobody has to find a
config file. Stored in <data folder>/settings.json; secret values encrypted
with SECRET_KEY like connection passwords, and never returned by the API.

Precedence: an environment variable (or the .env file) wins and shows as
locked in the app, so a deployment that manages SFS through its environment
keeps control; then the value saved in the app; then the default.

Each setting either applies at once (it is read when used) or needs a restart
(it is read when SFS starts). Deployment settings — data folders, metadata
database, SECRET_KEY — stay in the environment: SFS needs them to start.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Settings, settings


@dataclass(frozen=True)
class Setting:
    key: str                    # also the environment variable that overrides it
    label: str
    group: str
    type: str                   # int | float | bool | size | budget | text | secret
    help: str
    default: Any = None         # used when unset; None = "automatic" (see help)
    applies: str = "now"        # now | restart
    placeholder: str = ""
    minimum: float | None = None
    choices: tuple = field(default=())


DEFS: list[Setting] = [
    # ── Memory & performance ──
    Setting("SFS_MEMORY_BUDGET", "Memory budget", "Memory & performance", "budget",
            "When SFS uses more memory than this, running flows are stopped with a message, "
            "instead of the machine running out of memory. Blank: 80% of this machine's memory "
            "(or the container's limit). A size such as 12GB, a share such as 60%, or off.",
            placeholder="80%"),
    Setting("SFS_DUCKDB_MEMORY_LIMIT", "DuckDB memory limit", "Memory & performance", "size",
            "Beyond this, DuckDB nodes spill to disk instead of using more memory. Blank: half the "
            "memory budget. A node's own Memory limit (in its properties) overrides it.",
            placeholder="half the budget"),
    Setting("SFS_DUCKDB_TEMP_DIR", "DuckDB spill folder", "Memory & performance", "text",
            "Where DuckDB writes data that doesn't fit in memory. Blank: DuckDB's default.",
            placeholder="DuckDB default"),
    Setting("POLARS_MAX_THREADS", "Polars threads", "Memory & performance", "int",
            "CPU threads Polars uses. Fewer threads also means fewer batches in memory at once. "
            "Blank: all cores.", applies="restart", placeholder="all cores", minimum=1),
    Setting("SFS_WRITE_CHUNK_ROWS", "fast_write batch size (rows)", "Memory & performance", "int",
            "Rows fast_write sends per batch. Larger batches use a little more memory and rarely "
            "speed things up. Blank: 200,000 for PostgreSQL, 100,000 for ClickHouse, 50,000 others. "
            "A chunk_rows= argument in the code overrides it.", placeholder="automatic", minimum=1000),
    Setting("SFS_PARALLEL_BRANCHES", "Run independent branches in parallel", "Memory & performance", "bool",
            "Nodes that don't depend on each other run at the same time: faster on several cores, "
            "but their memory use adds up.", default=False),
    # ── Explore ──
    Setting("EXPLORE_ROW_CAP", "Rows per chart query", "Explore", "int",
            "The most rows a chart query returns.", default=50_000, minimum=100),
    Setting("EXPLORE_TIMEOUT_S", "Query time limit (seconds)", "Explore", "float",
            "A chart query running longer than this is stopped.", default=30.0, minimum=1),
    # ── Notifications (failure alerts of scheduled runs) ──
    Setting("SLACK_WEBHOOK_URL", "Slack webhook URL", "Failure alerts", "secret",
            "Scheduled runs that fail post here. Blank: no Slack alerts."),
    Setting("SMTP_HOST", "SMTP server", "Failure alerts", "text",
            "Mail server for failure e-mails. Blank: no e-mail alerts."),
    Setting("SMTP_PORT", "SMTP port", "Failure alerts", "int", "", default=587, minimum=1),
    Setting("SMTP_USER", "SMTP user", "Failure alerts", "text", "Blank: send without logging in."),
    Setting("SMTP_PASSWORD", "SMTP password", "Failure alerts", "secret", ""),
    Setting("SMTP_FROM", "Sender address", "Failure alerts", "text", "", default="sfs@localhost"),
]
BY_KEY = {d.key: d for d in DEFS}
_FIELDS = set(Settings.model_fields)          # applied to `settings`; the rest to os.environ


# ── where values come from ───────────────────────────────────────────────────

def _env_file_values() -> dict[str, str]:
    path = settings.model_config.get("env_file")
    try:
        from dotenv import dotenv_values
        return {k: v for k, v in dotenv_values(path).items() if v is not None} if path and Path(path).is_file() else {}
    except Exception:
        return {}


def _snapshot_env() -> dict[str, str]:
    """Values set by the environment when SFS started. Taken before anything
    here writes os.environ, so the app's own values never look 'locked'."""
    found = {**_env_file_values(), **os.environ}
    return {d.key: found[d.key] for d in DEFS if str(found.get(d.key, "")).strip()}


_ENV = _snapshot_env()
_AT_START: dict[str, Any] = {}                 # restart settings as this process applied them


def _path() -> Path:
    from app.packs.store import data_dir
    return data_dir() / "settings.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _stored(key: str) -> Any:
    v = _load().get(key)
    if v is not None and BY_KEY[key].type == "secret":
        from app.services.connection_service import decrypt
        try:
            return decrypt(v)
        except Exception:
            return None                        # SECRET_KEY changed: treat as unset
    return v


def source(key: str) -> str:
    if key in _ENV:
        return "environment"
    return "app" if _load().get(key) is not None else "default"


def get(key: str) -> Any:
    """The setting's value, typed: environment, then the app, then the default."""
    d = BY_KEY[key]
    if key in _ENV:
        try:
            return _coerce(d, _ENV[key])
        except ValueError:
            return d.default
    v = _stored(key)
    return d.default if v is None else v


# ── validation ───────────────────────────────────────────────────────────────

def _coerce(d: Setting, raw: Any) -> Any:
    """Raw input → stored value; None/blank means unset. Raises ValueError."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if d.type == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if d.type in ("int", "float"):
        try:
            v = int(raw) if d.type == "int" else float(raw)
        except (TypeError, ValueError):
            raise ValueError("must be a number") from None
        if d.type == "int" and isinstance(raw, float) and raw != int(raw):
            raise ValueError("must be a whole number")
        if d.minimum is not None and v < d.minimum:
            raise ValueError(f"must be at least {d.minimum:g}")
        return v
    s = str(raw).strip()
    if d.type == "size":
        from app.core.memory import parse_size
        parse_size(s)
        return s
    if d.type == "budget":
        if s.lower() == "off":
            return "off"
        if s.endswith("%"):
            try:
                pct = float(s[:-1])
            except ValueError:
                raise ValueError("use a share such as 60%") from None
            if not 10 <= pct <= 95:
                raise ValueError("a share must be between 10% and 95%")
            return f"{pct:g}%"
        from app.core.memory import parse_size
        if parse_size(s) < 256 << 20:
            raise ValueError("must be at least 256MB")
        return s
    return s


def update(values: dict[str, Any]) -> None:
    """Validate and save; apply what applies now. Raises ValueError with
    {key: message} when anything is invalid (nothing is saved then)."""
    errors, clean = {}, {}
    for key, raw in values.items():
        d = BY_KEY.get(key)
        if d is None:
            errors[key] = "unknown setting"
        elif key in _ENV:
            errors[key] = f"set by the environment ({key}); change it there"
        else:
            try:
                clean[key] = _coerce(d, raw)
            except ValueError as e:
                errors[key] = str(e)
    if errors:
        raise ValueError(errors)
    data = _load()
    for key, v in clean.items():
        if v is None or v == BY_KEY[key].default:    # the default is not a saved setting
            data.pop(key, None)
        elif BY_KEY[key].type == "secret":
            from app.services.connection_service import encrypt
            data[key] = encrypt(v)
        else:
            data[key] = v
    _save(data)
    for key in clean:
        if BY_KEY[key].applies == "now":
            _apply(key)


def _apply(key: str) -> None:
    """Make the effective value visible where SFS reads it."""
    d, v = BY_KEY[key], get(key)
    if key in _FIELDS:
        setattr(settings, key, d.default if v is None else v)
    elif v is None or v is False:
        os.environ.pop(key, None)
    else:
        os.environ[key] = "1" if v is True else str(v)


def apply_all() -> None:
    """At startup, before anything reads these (Polars reads its thread
    count when it is first imported)."""
    for d in DEFS:
        if d.key in _ENV and d.key not in _FIELDS:
            continue                           # already in os.environ
        try:
            _apply(d.key)
        except Exception:
            pass
        if d.applies == "restart":
            _AT_START[d.key] = get(d.key)


def restart_needed() -> list[str]:
    return [BY_KEY[k].label for k, v in _AT_START.items() if get(k) != v]


def view() -> dict:
    from app.core import memory, memory_guard, restart
    out = []
    for d in DEFS:
        src = source(d.key)
        v = get(d.key)
        item = {"key": d.key, "label": d.label, "group": d.group, "type": d.type, "help": d.help,
                "applies": d.applies, "placeholder": d.placeholder, "default": d.default,
                "source": src, "locked": src == "environment"}
        if d.type == "secret":
            item["is_set"] = bool(v)           # never the value itself
        else:
            item["value"] = _load().get(d.key) if src == "app" else (v if src == "environment" else None)
        out.append(item)
    return {"settings": out, "restart_needed": restart_needed(), "can_restart": restart.can_restart(),
            "memory": {"total": memory.total_bytes(), "used": memory.process_bytes(),
                       "budget": memory_guard.budget_bytes()}}
