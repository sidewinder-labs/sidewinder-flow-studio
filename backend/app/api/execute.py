"""Execute API."""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.core.database import get_db
from app.core.frame_cache import load_frame_head, get_frame_meta, clear_flow_cache
from app.engine.executor import execute_flow
from app.services.connection_service import list_connections, build_url, get_connect_args

router = APIRouter()


# All per-connection maps come from connection_maps(), which isolates failures:
# a connection with unusable credentials no longer breaks flows that don't use it.

async def _maps(db: AsyncSession) -> dict:
    from app.services.connection_service import connection_maps
    return connection_maps(await list_connections(db))


async def _build_connection_urls(db: AsyncSession) -> dict[str, str]:
    return (await _maps(db))["urls"]


async def _build_connection_cargs(db: AsyncSession) -> dict[str, dict]:
    return (await _maps(db))["cargs"]


async def _build_connection_dialects(db: AsyncSession) -> dict[str, str]:
    return (await _maps(db))["dialects"]


async def _build_storage_options(db: AsyncSession) -> dict[str, dict]:
    return (await _maps(db))["storage"]


class RunRequest(BaseModel):
    project_id: str
    flow_id: str
    # Send the full current flow state from the frontend so we always
    # run the live canvas — not a potentially stale on-disk version.
    flow_state: dict[str, Any]
    start_from_node: str | None = None
    resume_from_node: str | None = None   # load persisted checkpoint, run descendants


@router.post("/run")
async def run_flow(
    body: RunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # Anything that fails before the background task starts must come back as
    # a readable message — otherwise the UI just sees an opaque 500.
    try:
        connection_urls = await _build_connection_urls(db)
        storage_options = await _build_storage_options(db)
        connection_dialects = await _build_connection_dialects(db)
        connection_cargs = await _build_connection_cargs(db)
    except Exception as exc:
        raise HTTPException(status_code=500,
                            detail=f"Could not prepare the run: {type(exc).__name__}: {exc}")

    async def _run():
        # Record every run in the audit history (manual trigger, single attempt)
        import uuid, time as _t
        from datetime import datetime, UTC
        from app.core.database import AsyncSessionLocal
        from app.models.run_history import RunHistory

        run_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as hdb:
            hdb.add(RunHistory(
                id=run_id, project_id=body.project_id, flow_id=body.flow_id,
                flow_name=body.flow_state.get("name"), trigger="manual", status="running",
            ))
            await hdb.commit()

        t0 = _t.perf_counter()
        results = []
        err = None
        try:
            results = await execute_flow(
                project_id=body.project_id,
                flow=body.flow_state,
                connection_urls=connection_urls,
                variables={},
                start_from_node=body.start_from_node,
                resume_from_node=body.resume_from_node,
                storage_options=storage_options,
                connection_dialects=connection_dialects,
                connection_cargs=connection_cargs,
            )
        except Exception as e:
            err = str(e)

        n_err = sum(1 for r in results if r.get("status") == "error")
        async with AsyncSessionLocal() as hdb:
            row = await hdb.get(RunHistory, run_id)
            if row:
                row.status = "error" if (err or n_err) else (
                    "stopped" if any(r.get("status") == "stopped" for r in results) else "ok")
                row.finished_at = datetime.now(UTC)
                row.duration_ms = round((_t.perf_counter() - t0) * 1000)
                row.nodes_ok = sum(1 for r in results if r.get("status") == "ok")
                row.nodes_error = n_err
                row.error = err or next((r.get("detail") for r in results if r.get("status") == "error"), None)
                await hdb.commit()

    background_tasks.add_task(_run)
    return {"status": "started", "flow_id": body.flow_id}


class FlushPersistRequest(BaseModel):
    table_name: str
    target: str = "local"   # "local" or a connection name


@router.post("/flush-persist")
async def flush_persist(body: FlushPersistRequest, db: AsyncSession = Depends(get_db)):
    from app.core import report_store
    if body.target == "local":
        report_store.drop_persist(body.table_name)
    else:
        import sqlalchemy as sa
        urls = await _build_connection_urls(db)
        url = urls.get(body.target)
        if not url:
            raise HTTPException(404, f"Connection '{body.target}' not found")
        eng = sa.create_engine(url)
        tbl = report_store.sanitize_table(body.table_name)
        with eng.begin() as conn:
            conn.execute(sa.text(f'DROP TABLE IF EXISTS "{tbl}"'))
    return {"flushed": body.table_name}


@router.post("/clear-cache")
def clear_cache(project_id: str, flow_id: str):
    clear_flow_cache(project_id)
    return {"status": "cleared"}


@router.get("/frame/{project_id}/{node_id}")
def inspect_frame(project_id: str, node_id: str, limit: int = 500):
    meta = get_frame_meta(project_id, node_id)
    if not meta:
        raise HTTPException(404, "No cached frame for this node")
    try:
        # Only the rows shown: loading a whole 30M-row frame to show 500 is slow.
        df = load_frame_head(project_id, node_id, limit)
    except FileNotFoundError:
        raise HTTPException(404, "Frame not found")

    import json
    schema = [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns]
    rows = json.loads(df.to_json(orient="records", date_format="iso"))
    return {"schema": schema, "rows": rows, "total": meta["rows"], "meta": meta}


@router.get("/running")
def running(project_id: str):
    """Flows of the project executing now, in this backend process. After a
    restart the browser asks, so a run that died with the old process stops
    showing as running."""
    from app.engine.active_runs import running_flows
    return {"flow_ids": sorted(running_flows(project_id))}


@router.get("/estimate/{project_id}")
def estimate(project_id: str, node_ids: str):
    """What a pandas node with these inputs would load (the node panel shows
    it before running): rows and memory per input, and the room left now."""
    from app.core import memory, memory_guard
    from app.core.frame_cache import pandas_estimate
    inputs = []
    for nid in [n for n in node_ids.split(",") if n]:
        e = pandas_estimate(project_id, nid)
        if e:
            inputs.append({"node_id": nid, **e})
    free, why = memory_guard.room()
    return {"inputs": inputs, "rows": sum(i["rows"] for i in inputs),
            "load_bytes": sum(i["load_bytes"] for i in inputs),
            "room": free, "room_why": why, "budget": memory_guard.budget_bytes(),
            "total": memory.total_bytes()}
