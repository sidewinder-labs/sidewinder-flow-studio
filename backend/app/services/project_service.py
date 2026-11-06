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


# ── Canonical file format ─────────────────────────────────────────────────────
# Saving an unchanged project writes a byte-identical file, so exports and any
# future Git integration diff cleanly. The canvas hands back its own state with
# the nodes (selection, drag, measured size); none of it belongs in the file.

_NODE_TRANSIENT = ("selected", "dragging", "positionAbsolute", "width", "height",
                   "measured", "resizing", "internals")
_EDGE_TRANSIENT = ("selected",)
_FLOW_TRANSIENT = ("next_run",)            # attached by GET /projects/{id}


def _whole(v):
    return round(v) if isinstance(v, float) else v


def canonical_flow(flow: dict) -> dict:
    """A flow as it is stored: no canvas state, whole-pixel positions, nodes and
    edges ordered by id. Code stays as plain strings."""
    f = {k: v for k, v in flow.items() if k not in _FLOW_TRANSIENT}
    nodes = []
    for n in f.get("nodes") or []:
        n = {k: v for k, v in n.items() if k not in _NODE_TRANSIENT}
        if isinstance(n.get("position"), dict):
            n["position"] = {k: _whole(v) for k, v in n["position"].items()}
        if isinstance(n.get("style"), dict):
            n["style"] = {k: _whole(v) for k, v in n["style"].items()}
        nodes.append(n)
    edges = [{k: v for k, v in e.items() if k not in _EDGE_TRANSIENT} for e in f.get("edges") or []]
    f["nodes"] = sorted(nodes, key=lambda n: str(n.get("id", "")))
    f["edges"] = sorted(edges, key=lambda e: str(e.get("id", "")))
    return f


def canonical(project: dict) -> dict:
    p = dict(project)
    p["flows"] = [canonical_flow(f) for f in project.get("flows") or []]
    return p


def dumps(data: dict) -> str:
    """The one serializer for project files: sorted keys, two-space indent,
    readable non-ASCII, trailing newline."""
    return json.dumps(canonical(data), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _write(p: Path, data: dict) -> None:
    p.write_text(dumps(data), encoding="utf-8")


def list_projects() -> list[dict]:
    out = []
    for f in sorted(_dir().glob("*.json")):
        try:
            d = _read(f)
            out.append({"id": d["id"], "name": d["name"],
                        "description": d.get("description", ""),
                        "updated_at": d.get("updated_at")})
        except Exception:
            pass
    return out


def get_project(pid: str) -> dict | None:
    p = _path(pid)
    return canonical(_read(p)) if p.exists() else None


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
    _write(_path(data["id"]), data)
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
    _write(_path(pid), data)
    return data


def save_project(pid: str, payload: dict) -> dict | None:
    p = _path(pid)
    if not p.exists():
        return None
    existing = canonical(_read(p))
    updated = canonical({**existing,
        "flows": payload.get("flows", existing["flows"]),
        "name": payload.get("name", existing["name"]),
        "description": payload.get("description", existing.get("description", "")),
    })
    if updated == existing:
        # Nothing changed: leave the file (and updated_at) exactly as it was,
        # rewriting only a file saved before the canonical format existed.
        if p.read_text(encoding="utf-8") != dumps(existing):
            _write(p, existing)
        return existing
    updated["updated_at"] = datetime.now(UTC).isoformat()
    _write(p, updated)
    return updated


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
    data = _read(p)
    data["deleted_at"] = datetime.now(UTC).isoformat()
    _write(_trash_path(pid), data)
    p.unlink()
    return True


def list_trash() -> list[dict]:
    out = []
    for f in _trash_dir().glob("*.json"):
        try:
            d = _read(f)
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
    data = _read(t)
    data.pop("deleted_at", None)
    for flow in data.get("flows", []):
        flow["active"] = False
    data["updated_at"] = datetime.now(UTC).isoformat()
    _write(_path(pid), data)
    t.unlink()
    return data


def purge_project(pid: str) -> bool:
    """Delete a trashed project for good."""
    t = _trash_path(pid)
    if not t.exists():
        return False
    t.unlink()
    return True
