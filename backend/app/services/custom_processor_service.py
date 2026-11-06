"""Custom (saved) processors — reusable processor snippets a user can save and
drag into any flow. Stored as a single JSON file alongside projects."""
import json
import uuid
from datetime import datetime, UTC
from pathlib import Path

from app.core.config import settings


def _store_path() -> Path:
    d = Path(settings.PROJECTS_DIR).parent
    d.mkdir(parents=True, exist_ok=True)
    return d / "custom_processors.json"


def _load() -> list[dict]:
    p = _store_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    _store_path().write_text(json.dumps(items, indent=2))


def list_custom() -> list[dict]:
    return sorted(_load(), key=lambda x: x.get("name", "").lower())


def create_custom(data: dict) -> dict:
    items = _load()
    item = {
        "id": str(uuid.uuid4()),
        "name": data["name"],
        "description": data.get("description", ""),
        "engine": data.get("engine", "pandas"),
        "code": data.get("code", ""),
        "created_at": datetime.now(UTC).isoformat(),
    }
    items.append(item)
    _save(items)
    return item


def delete_custom(cid: str) -> bool:
    items = _load()
    remaining = [i for i in items if i["id"] != cid]
    if len(remaining) == len(items):
        return False
    _save(remaining)
    return True


def import_many(items: list[dict]) -> int:
    """Add exported custom processors whose id isn't here yet; returns how many."""
    current = _load()
    have = {i["id"] for i in current}
    new = [i for i in items if i.get("id") and i["id"] not in have and i.get("name")]
    _save(current + new)
    return len(new)
