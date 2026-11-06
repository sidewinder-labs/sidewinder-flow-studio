"""Function library API: reusable functions copied into flows (see
app/services/function_library.py). Operations on a flow take its current
Functions and Imports text, so unsaved edits in the editor are respected; the
frontend stores what comes back."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import function_library as lib

router = APIRouter()


def _err(exc: Exception):
    if isinstance(exc, KeyError):
        return HTTPException(404, "No such library entry")
    msg = str(exc)
    if msg.startswith("conflict:"):
        return HTTPException(409, {"conflict": msg.split(":", 1)[1]})
    return HTTPException(400, msg)


class EntryIn(BaseModel):
    name: str
    code: str
    description: str = ""
    imports: str = ""
    tags: list[str] = []


class EntryEdit(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    imports: str | None = None
    tags: list[str] | None = None


class FlowText(BaseModel):
    functions: str = ""
    imports: str = ""
    provenance: dict = {}


class InsertIn(FlowText):
    entry_id: str
    on_conflict: str = "ask"          # ask | replace | rename
    new_name: str | None = None


class UpdateCopyIn(FlowText):
    name: str


class ProposeIn(BaseModel):
    code: str
    flow_imports: str = ""


class DeprecateIn(BaseModel):
    note: str = ""
    deprecated: bool = True


@router.get("/")
def list_entries(include_deprecated: bool = True):
    return lib.list_entries(include_deprecated)


@router.post("/", status_code=201)
def create(body: EntryIn):
    try:
        return lib.create(**body.model_dump())
    except lib.LibraryError as exc:
        raise _err(exc)


@router.put("/{entry_id}")
def edit(entry_id: str, body: EntryEdit):
    try:
        return lib.update(entry_id, **body.model_dump())
    except (lib.LibraryError, KeyError) as exc:
        raise _err(exc)


@router.post("/{entry_id}/deprecate")
def deprecate(entry_id: str, body: DeprecateIn):
    try:
        return lib.deprecate(entry_id, body.note, body.deprecated)
    except KeyError as exc:
        raise _err(exc)


@router.delete("/{entry_id}", status_code=204)
def delete(entry_id: str):
    if not lib.delete(entry_id):
        raise HTTPException(404, "No such library entry")


@router.post("/functions")
def functions_in(body: FlowText):
    """The flow's top-level functions, with each library copy's state."""
    try:
        blocks = lib.top_level(body.functions)
        states = lib.copy_states(body.functions, lib.prune(body.functions, body.provenance))
    except lib.LibraryError as exc:
        return {"error": str(exc), "functions": []}
    return {"functions": [{"name": b["name"], "kind": b["kind"], "source": b["source"],
                           **({"library": states[b["name"]]} if b["name"] in states else {})}
                          for b in blocks]}


@router.post("/propose-imports")
def propose(body: ProposeIn):
    try:
        return {"imports": lib.propose_imports(body.code, body.flow_imports)}
    except lib.LibraryError as exc:
        raise _err(exc)


@router.post("/insert")
def insert(body: InsertIn):
    try:
        return lib.insert(functions=body.functions, imports=body.imports, provenance=body.provenance,
                          entry_id=body.entry_id, on_conflict=body.on_conflict, new_name=body.new_name)
    except (lib.LibraryError, KeyError) as exc:
        raise _err(exc)


@router.post("/update-copy")
def update_copy(body: UpdateCopyIn):
    try:
        return lib.update_copy(functions=body.functions, imports=body.imports,
                               provenance=body.provenance, name=body.name)
    except lib.LibraryError as exc:
        raise _err(exc)


@router.get("/export")
def export_library():
    return lib.export_all()


@router.post("/import")
def import_library(body: dict):
    try:
        return lib.import_all(body)
    except lib.LibraryError as exc:
        raise _err(exc)
