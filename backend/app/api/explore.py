"""Explore API — Graphic Walker server-side computation.

    GET  /api/explore/sources                cached frames, persisted tables, connections
    GET  /api/explore/schemas ?connection=…  visible schemas of a live connection
    GET  /api/explore/tables  ?connection=…[&schema=…|&q=…]  tables and views (one schema, or a search)
    PUT  /api/explore/browser-settings { connection, settings }
    GET  /api/explore/schema  ?kind=frame&project_id=…&node_id=…
                              ?kind=persist&table=…
                              ?kind=table&connection=…&table=…
                              ?kind=sql&connection=…&sql=…
    POST /api/explore/query   { source: {...}, payload: <GW DSL> }
    POST /api/explore/node    { source, payload, mode: dataset|chart, engine? } → processor node
    GET  /api/explore/drill-config ?kind=…&…   PUT { source, config|null }
    POST /api/explore/drill   { source, payload, row } → detail rows
"""
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.engine.executor import SQLGLOT_DIALECTS
from app.explore import catalog, drill, live, nodegen, sources
from app.explore.compiler import CompileError
from app.services.connection_service import (BROWSER_DEFAULTS, browser_settings, connection_maps,
                                             list_connections, update_connection)

router = APIRouter()

LIVE = ("table", "sql")


class Source(BaseModel):
    kind: str                       # frame | persist | table | sql
    project_id: str | None = None
    node_id: str | None = None
    connection: str | None = None
    table: str | None = None        # persist: table name; table: [schema.]table
    sql: str | None = None          # sql: a SELECT in the connection's dialect

    def cached(self) -> dict:
        return {"project_id": self.project_id, "node_id": self.node_id} if self.kind == "frame" \
            else {"table": self.table}

    def live(self) -> dict:
        return {"table": self.table} if self.kind == "table" else {"sql": self.sql}

    def key(self) -> str:
        """Same as the frontend's sourceKey() for cached sources."""
        return f"frame:{self.project_id}:{self.node_id}" if self.kind == "frame" else f"{self.kind}:{self.table}"


class QueryIn(BaseModel):
    source: Source
    payload: dict


class NodeIn(BaseModel):
    source: Source
    payload: dict
    mode: str = "dataset"           # dataset | chart
    engine: str | None = None       # live sources: sql | pandas


class DrillConfigIn(BaseModel):
    source: Source
    config: dict | None = None


class DrillIn(BaseModel):
    source: Source
    payload: dict
    row: dict


async def _engine(db: AsyncSession, name: str | None):
    """Pooled SQLAlchemy engine + SQLGlot dialect for a named SFS connection."""
    maps = connection_maps(await list_connections(db))
    if not name or name not in maps["urls"]:
        raise HTTPException(404, f"Connection '{name}' not found")
    if name in maps["broken"]:
        raise HTTPException(400, maps["broken"][name])
    dialect = maps["dialects"][name]
    eng = live.engine_for(name, maps["urls"][name], maps["cargs"].get(name))
    return eng, SQLGLOT_DIALECTS.get(dialect, dialect)


# A chart sends a query per interaction, and the compiler needs column types.
# Re-reading the catalog (or re-sampling a SQL source) each time is wasted work,
# so live schemas are kept briefly.
_SCHEMA_TTL_S = 60
_schemas: dict[tuple, tuple[float, dict]] = {}


def _live_schema(eng, dialect: str, s: Source) -> dict:
    key = (s.connection, s.kind, s.table, s.sql)
    hit = _schemas.get(key)
    if hit and time.monotonic() - hit[0] < _SCHEMA_TTL_S:
        return hit[1]
    out = live.live_schema(eng, dialect, s.kind, **s.live())
    _schemas[key] = (time.monotonic(), out)
    return out


def _db_error(prefix: str, exc: Exception) -> HTTPException:
    # Database errors (bad column, missing table, credentials) are about the
    # source or the chart, not a server fault: return the message, not a 500.
    return HTTPException(400, f"{prefix}: {str(exc).strip().splitlines()[0][:500]}")


@router.get("/sources")
async def list_sources(db: AsyncSession = Depends(get_db)):
    out = await run_in_threadpool(sources.list_cached)
    # Names and dialects only — credentials never leave the connection service.
    # Browser settings are non-secret (they export with the connection).
    out["connections"] = [{"name": c.name, "dialect": c.dialect, "browser": browser_settings(c)}
                          for c in await list_connections(db)]
    return out


async def _browser(db: AsyncSession, name: str) -> dict:
    c = next((c for c in await list_connections(db) if c.name == name), None)
    return browser_settings(c) if c else dict(BROWSER_DEFAULTS)


@router.get("/schemas")
async def list_schemas(connection: str, db: AsyncSession = Depends(get_db)):
    """Schemas first; their objects load on expand (GET /tables?schema=)."""
    eng, dialect = await _engine(db, connection)
    s = await _browser(db, connection)
    try:
        return {"schemas": await run_in_threadpool(catalog.schemas, eng, dialect, s), "settings": s}
    except Exception as exc:
        raise _db_error(f"Could not list schemas on {connection}", exc)


@router.get("/tables")
async def list_tables(connection: str, schema: str | None = None, q: str | None = None,
                      db: AsyncSession = Depends(get_db)):
    """Objects of one schema, a search across schemas (q, run in the database),
    or with neither, every visible object (capped)."""
    eng, dialect = await _engine(db, connection)
    s = await _browser(db, connection)
    try:
        return await run_in_threadpool(catalog.objects, eng, dialect, s, schema, q)
    except Exception as exc:
        raise _db_error(f"Could not list tables on {connection}", exc)


class BrowserIn(BaseModel):
    connection: str
    settings: dict


@router.put("/browser-settings")
async def put_browser_settings(body: BrowserIn, db: AsyncSession = Depends(get_db)):
    c = next((c for c in await list_connections(db) if c.name == body.connection), None)
    if not c:
        raise HTTPException(404, f"Connection '{body.connection}' not found")
    clean = {k: body.settings[k] for k in BROWSER_DEFAULTS if k in body.settings}
    for k in ("include_schemas", "exclude_schemas"):
        if k in clean:
            v = clean[k]
            clean[k] = [x.strip() for x in (v.split(",") if isinstance(v, str) else v or []) if str(x).strip()]
    c = await update_connection(db, c.id, {"browser": {**browser_settings(c), **clean}})
    return {"settings": browser_settings(c)}


@router.get("/schema")
async def schema(kind: str, project_id: str | None = None, node_id: str | None = None,
                 connection: str | None = None, table: str | None = None, sql: str | None = None,
                 db: AsyncSession = Depends(get_db)):
    s = Source(kind=kind, project_id=project_id, node_id=node_id, connection=connection, table=table, sql=sql)
    try:
        if kind in ("frame", "persist"):
            return await run_in_threadpool(sources.cached_schema, kind, **s.cached())
        if kind in LIVE:
            eng, dialect = await _engine(db, connection)
            _schemas.pop((s.connection, s.kind, s.table, s.sql), None)     # opening a source re-reads it
            return await run_in_threadpool(_live_schema, eng, dialect, s)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except live.SourceError as exc:
        raise HTTPException(400, str(exc))
    except sources.QueryTimeout as exc:
        raise HTTPException(408, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error("Could not read schema", exc)
    raise HTTPException(400, f"Unknown source kind '{kind}'")


@router.post("/query")
async def query(body: QueryIn, db: AsyncSession = Depends(get_db)):
    s = body.source
    dialect = "duckdb"
    try:
        if s.kind in ("frame", "persist"):
            r = await run_in_threadpool(lambda: sources.cached_query(s.kind, body.payload, **s.cached()))
        elif s.kind in LIVE:
            eng, dialect = await _engine(db, s.connection)

            def run():
                fields = _live_schema(eng, dialect, s)["fields"]
                return live.live_query(eng, dialect, s.kind, body.payload, fields, **s.live())
            r = await run_in_threadpool(run)
        else:
            raise HTTPException(400, f"Unknown source kind '{s.kind}'")
    except CompileError as exc:
        raise HTTPException(422, f"Could not compile chart query: {exc}")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except live.SourceError as exc:
        raise HTTPException(400, str(exc))
    except sources.QueryTimeout as exc:
        raise HTTPException(408, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error("Query failed", exc)
    return {"rows": r.rows, "sql": r.sql, "truncated": r.truncated, "row_cap": settings.EXPLORE_ROW_CAP,
            "dialect": dialect, "compile_ms": round(r.compile_ms, 2), "exec_ms": round(r.exec_ms, 1)}


# ── Explore → flow ────────────────────────────────────────────────────────────

def _frame_label(project_id: str, node_id: str) -> str:
    from app.services import project_service
    for flow in (project_service.get_project(project_id) or {}).get("flows", []):
        for n in flow.get("nodes", []):
            if n.get("id") == node_id:
                return (n.get("data") or {}).get("label") or n.get("type") or node_id
    return node_id


@router.post("/node")
async def make_node(body: NodeIn, db: AsyncSession = Depends(get_db)):
    s = body.source
    try:
        if s.kind == "frame":
            label = await run_in_threadpool(_frame_label, s.project_id, s.node_id)
            fields = (await run_in_threadpool(sources.cached_schema, "frame", **s.cached()))["fields"]
            return nodegen.make_node("frame", body.payload, fields, mode=body.mode, label=label,
                                     input_hint=f"connect “{label}” to this node; its output arrives as table df")
        if s.kind == "persist":
            fields = (await run_in_threadpool(sources.cached_schema, "persist", **s.cached()))["fields"]
            return nodegen.make_node("persist", body.payload, fields, mode=body.mode, label=s.table,
                                     input_hint=f"connect the node that persists “{s.table}”; "
                                                "its output arrives as table df")
        if s.kind in LIVE:
            eng, dialect = await _engine(db, s.connection)
            fields = (await run_in_threadpool(_live_schema, eng, dialect, s))["fields"]
            label = s.table if s.kind == "table" else f"SQL on {s.connection}"
            return nodegen.make_node(s.kind, body.payload, fields, mode=body.mode, label=label, engine=body.engine,
                                     dialect=dialect, connection=s.connection, **s.live())
    except (ValueError, CompileError) as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error("Could not build the node", exc)
    raise HTTPException(400, f"Unknown source kind '{s.kind}'")


# ── drill-through (cached sources → a live detail table) ──────────────────────

def _cached_only(s: Source):
    if s.kind not in ("frame", "persist"):
        raise HTTPException(400, "Drill-through is set on cached sources (node outputs and persisted tables)")


@router.get("/drill-config")
async def get_drill_config(kind: str, project_id: str | None = None, node_id: str | None = None,
                           table: str | None = None):
    s = Source(kind=kind, project_id=project_id, node_id=node_id, table=table)
    _cached_only(s)
    return {"config": drill.get_config(s.key())}


@router.put("/drill-config")
async def put_drill_config(body: DrillConfigIn):
    _cached_only(body.source)
    try:
        return {"config": drill.set_config(body.source.key(), body.config)}
    except drill.DrillError as exc:
        raise HTTPException(400, str(exc))


@router.post("/drill")
async def run_drill(body: DrillIn, db: AsyncSession = Depends(get_db)):
    s = body.source
    _cached_only(s)
    config = drill.get_config(s.key())
    if not config:
        raise HTTPException(400, "No detail source set for this data — set one with Drill-through settings")
    try:
        fields = (await run_in_threadpool(sources.cached_schema, s.kind, **s.cached()))["fields"]
        eng, dialect = await _engine(db, config["connection"])
        return await run_in_threadpool(drill.run_drill, eng, dialect, config, body.payload, body.row, fields)
    except drill.DrillError as exc:
        raise HTTPException(400, str(exc))
    except sources.QueryTimeout as exc:
        raise HTTPException(408, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error("Drill query failed", exc)
