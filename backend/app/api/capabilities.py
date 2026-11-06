"""
Capabilities API — what this SFS instance can do.

The frontend calls this to render available engines (including any added by
plugins) instead of hard-coding a list, so installing a plugin surfaces its
engine in the UI without a frontend change.
"""
import importlib.util
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engine import executor as _executor  # noqa: F401 — registers builtins
from app.engine.registry import ENGINES, list_engines, list_examples, list_templates, loaded_plugins
from app.help.examples import list_examples as list_snippets
from app.help.reference import helper_reference, library_reference

router = APIRouter()


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


@router.get("/")
async def get_capabilities():
    engines = list_engines()
    return {
        "engines": engines,
        "plugins": loaded_plugins(),
        "templates": list_templates(),
        "examples": list_examples(),
        # In-app help: the function reference (from the helpers' own signatures
        # and docstrings) and ready-to-insert code examples
        "helpers": helper_reference() + library_reference(),
        "snippets": list_snippets(),
        "features": {
            # Optional capabilities the UI can gate on
            "enterprise_engines": any(
                e["tier"] == "enterprise" and e["available"] for e in engines
            ),
            # Reports open in the Streamlit viewer, which the desktop edition
            # doesn't ship
            "reports": _has("duckdb") and os.getenv("SFS_DESKTOP") != "1",
            "desktop": os.getenv("SFS_DESKTOP") == "1",
            "iceberg": _has("pyiceberg"),
            "deltalake": _has("deltalake"),
        },
    }


class ToCodeRequest(BaseModel):
    engine: str
    node_data: dict = {}
    project_id: str | None = None
    parent_ids: list[str] = []


@router.post("/to-code")
async def to_code(req: ToCodeRequest):
    """View as Python: the engine's equivalent pandas-node code for this node."""
    spec = ENGINES.get(req.engine)
    if spec is None or spec.to_code is None:
        raise HTTPException(404, f"Engine '{req.engine}' has no View as Python")
    from app.core.frame_cache import has_frame, load_frame
    inputs = []
    if req.project_id:
        inputs = [load_frame(req.project_id, pid) for pid in req.parent_ids
                  if has_frame(req.project_id, pid)]
    try:
        code = spec.to_code(spec.resolve_params(req.node_data), inputs)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc).strip("'\"")) from None
    return {"code": code}
