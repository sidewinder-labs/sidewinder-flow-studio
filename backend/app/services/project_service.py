import json, uuid
from datetime import datetime, UTC
from pathlib import Path
from app.core.config import settings


def _dir() -> Path:
    p = Path(settings.PROJECTS_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(pid: str) -> Path:
    return _dir() / f"{pid}.json"


def list_projects() -> list[dict]:
    out = []
    for f in sorted(_dir().glob("*.json")):
        try:
            d = json.loads(f.read_text())
            out.append({"id": d["id"], "name": d["name"],
                        "description": d.get("description", ""),
                        "updated_at": d.get("updated_at")})
        except Exception:
            pass
    return out


def get_project(pid: str) -> dict | None:
    p = _path(pid)
    return json.loads(p.read_text()) if p.exists() else None


def create_project_from_flow(name: str, description: str, flow: dict) -> dict:
    """A new project whose first flow is a copy of `flow` (e.g. a plugin
    example). Node ids are kept — frames are cached per project — but the flow
    gets a fresh id. A Manual trigger is added if the flow has none."""
    import copy
    data = create_project(name, description)
    f = data["flows"][0]
    src = copy.deepcopy(flow)
    nodes = src.get("nodes") or []
    if any(n.get("type") == "trigger" for n in nodes):
        f["nodes"] = nodes
    else:
        f["nodes"] += nodes
    f["edges"] = src.get("edges") or []
    f["name"] = src.get("name") or f["name"]
    if src.get("imports"):
        f["imports"] = src["imports"]
    _path(data["id"]).write_text(json.dumps(data, indent=2))
    return data


def create_project(name: str, description: str = "") -> dict:
    pid = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    # Default first flow with a Manual trigger node pre-placed
    trigger_id = str(uuid.uuid4())
    data = {
        "id": pid, "name": name, "description": description,
        "created_at": now, "updated_at": now,
        "flows": [{
            "id": str(uuid.uuid4()),
            "name": "Flow 1",
            "active": True,
            "imports": "import pandas as pd\nimport sqlalchemy as sa\n",
            "nodes": [{
                "id": trigger_id,
                "type": "trigger",
                "position": {"x": 80, "y": 200},
                "data": {
                    "label": "Start",
                    "run_mode": "manual",
                    "cron": "",
                    "timezone": "UTC",
                }
            }],
            "edges": [],
        }],
    }
    _path(pid).write_text(json.dumps(data, indent=2))
    return data


def save_project(pid: str, payload: dict) -> dict | None:
    p = _path(pid)
    if not p.exists():
        return None
    existing = json.loads(p.read_text())
    existing.update({
        "flows": payload.get("flows", existing["flows"]),
        "name": payload.get("name", existing["name"]),
        "description": payload.get("description", existing.get("description", "")),
        "updated_at": datetime.now(UTC).isoformat(),
    })
    p.write_text(json.dumps(existing, indent=2))
    return existing


# ── Trash: deleted projects are moved aside, not removed ─────────────────────

def _trash_dir() -> Path:
    p = _dir() / ".trash"
    p.mkdir(exist_ok=True)
    return p


def _trash_path(pid: str) -> Path:
    return _trash_dir() / f"{pid}.json"


def delete_project(pid: str) -> bool:
    """Soft delete: move the project file to projects/.trash/, recording when."""
    p = _path(pid)
    if not p.exists():
        return False
    data = json.loads(p.read_text())
    data["deleted_at"] = datetime.now(UTC).isoformat()
    _trash_path(pid).write_text(json.dumps(data, indent=2))
    p.unlink()
    return True


def list_trash() -> list[dict]:
    out = []
    for f in _trash_dir().glob("*.json"):
        try:
            d = json.loads(f.read_text())
            out.append({"id": d["id"], "name": d["name"], "deleted_at": d.get("deleted_at"),
                        "flows": len(d.get("flows", []))})
        except Exception:
            pass
    return sorted(out, key=lambda d: d.get("deleted_at") or "", reverse=True)


def restore_project(pid: str) -> dict | None:
    """Move a project back from the trash. Its flows come back inactive, so no
    schedule starts firing until the user turns it on again."""
    t = _trash_path(pid)
    if not t.exists():
        return None
    if _path(pid).exists():
        raise FileExistsError(pid)
    data = json.loads(t.read_text())
    data.pop("deleted_at", None)
    for flow in data.get("flows", []):
        flow["active"] = False
    data["updated_at"] = datetime.now(UTC).isoformat()
    _path(pid).write_text(json.dumps(data, indent=2))
    t.unlink()
    return data


def purge_project(pid: str) -> bool:
    """Delete a trashed project for good."""
    t = _trash_path(pid)
    if not t.exists():
        return False
    t.unlink()
    return True
