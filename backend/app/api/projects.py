"""Projects API."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services import deletion, project_service
from app.scheduler.scheduler import get_next_run, sync_schedules

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectSave(BaseModel):
    flows: list[dict]
    name: str | None = None
    description: str | None = None
    # When this save removes flows: also drop their persisted tables
    drop_persisted: bool = False


@router.get("/")
def list_projects():
    return project_service.list_projects()


@router.post("/", status_code=201)
def create_project(body: ProjectCreate):
    return project_service.create_project(body.name, body.description)


class FromExample(BaseModel):
    example: str
    name: str | None = None


@router.post("/from-example", status_code=201)
def create_from_example(body: FromExample):
    from app.engine.registry import get_example, load_plugins
    load_plugins()
    ex = get_example(body.example)
    if ex is None:
        raise HTTPException(404, f"No example named '{body.example}'")
    return project_service.create_project_from_flow(
        body.name or ex["name"], ex["description"], ex["flow"])


# ── Trash (before /{pid}, which would match "trash") ────────────────────────

@router.get("/trash/")
def list_trash():
    return project_service.list_trash()


@router.post("/trash/{pid}/restore")
def restore_project(pid: str):
    try:
        project = project_service.restore_project(pid)
    except FileExistsError:
        raise HTTPException(409, "A project with this id already exists")
    if project is None:
        raise HTTPException(404, "Not in the trash")
    return project                              # flows come back inactive: nothing to schedule


@router.delete("/trash/{pid}", status_code=204)
def purge_project(pid: str):
    if not project_service.purge_project(pid):
        raise HTTPException(404, "Not in the trash")


@router.get("/{pid}/delete-preview")
def delete_preview(pid: str, flow_id: str | None = None):
    """What deleting the project, or one flow of it, would affect."""
    info = deletion.preview(pid, flow_id)
    if info is None:
        raise HTTPException(404, "Project not found")
    return info


@router.get("/{pid}")
def get_project(pid: str):
    p = project_service.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found")
    # Attach next_run info per flow
    for flow in p.get("flows", []):
        flow["next_run"] = get_next_run(flow["id"])
    return p


@router.put("/{pid}")
def save_project(pid: str, body: ProjectSave):
    # Flows missing from the save are being deleted, however the client removed them.
    going, staying = deletion.removed_flows(pid, body.flows)
    try:
        deletion.check_removal(pid, going)
    except deletion.Busy as exc:
        raise HTTPException(409, str(exc))
    tables = deletion.tables_to_drop(pid, going, staying) if going and body.drop_persisted else []

    # A function renamed or removed in the flow drops its library provenance
    from app.services.function_library import prune
    for flow in body.flows:
        if flow.get("function_provenance"):
            flow["function_provenance"] = prune(flow.get("shared_functions", ""), flow["function_provenance"])
    result = project_service.save_project(pid, body.model_dump(exclude_none=True, exclude={"drop_persisted"}))
    if not result:
        raise HTTPException(404, "Project not found")
    if going:
        deletion.remove_flows(pid, going, result.get("flows", []), tables)
    sync_schedules(pid, result.get("flows", []))
    return result


@router.delete("/{pid}")
def delete_project(pid: str, drop_persisted: bool = False):
    """Move the project to the trash, unschedule its flows and delete its
    cached frames; drop its persisted tables only when asked."""
    try:
        result = deletion.delete_project(pid, drop_persisted)
    except deletion.Busy as exc:
        raise HTTPException(409, str(exc))
    if result is None:
        raise HTTPException(404, "Project not found")
    return result
