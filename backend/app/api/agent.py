"""Agent API — code generation + AI provider management."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.frame_cache import has_frame, load_frame_head
from app.services.agent_service import generate_code
from app.services import agent_provider_service as provider_svc
from app.auth.permissions import needs

router = APIRouter()


# ── Code generation ───────────────────────────────────────────────────────────

class AgentRequest(BaseModel):
    project_id: str
    request: str
    node_type: str                        # "processor" or "chart"
    upstream_node_id: str | None = None
    engine: str = "pandas"                # "pandas" | "duckdb"


@router.post("/generate")
async def agent_generate(body: AgentRequest, db: AsyncSession = Depends(get_db)):
    schema = None
    sample = None

    if body.upstream_node_id and has_frame(body.project_id, body.upstream_node_id):
        try:
            df = load_frame_head(body.project_id, body.upstream_node_id, 3)   # all it uses
            schema = [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns]
            import json
            sample = json.loads(df.head(3).to_json(orient="records", date_format="iso", default_handler=str))
        except Exception:
            schema = None
            sample = None

    result = await generate_code(
        request=body.request,
        node_type=body.node_type,
        schema=schema,
        sample=sample,
        db=db,
        engine=body.engine,
    )
    return {
        "code": result.get("code"),
        "error": result.get("error"),
        "provider": result.get("provider"),
        "had_context": schema is not None,
    }


# ── Provider management ───────────────────────────────────────────────────────

class ProviderIn(BaseModel):
    name: str
    provider_type: str                    # anthropic | ollama | openai_compatible
    model: str
    base_url: str | None = None
    api_key: str | None = None
    is_active: bool = False


class ProviderUpdate(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None            # blank = keep existing


def _provider_out(p) -> dict:
    """Never expose the raw key — only whether one is set."""
    return {
        "id": p.id,
        "name": p.name,
        "provider_type": p.provider_type,
        "model": p.model,
        "base_url": p.base_url,
        "has_api_key": bool(p.api_key_enc),
        "is_active": p.is_active,
    }


@router.get("/providers")
async def list_providers(db: AsyncSession = Depends(get_db)):
    return [_provider_out(p) for p in await provider_svc.list_providers(db)]


@router.post("/providers", status_code=201)
@needs("settings.admin")
async def create_provider(body: ProviderIn, db: AsyncSession = Depends(get_db)):
    return _provider_out(await provider_svc.create_provider(db, body.model_dump()))


@router.put("/providers/{pid}")
@needs("settings.admin")
async def update_provider(pid: str, body: ProviderUpdate, db: AsyncSession = Depends(get_db)):
    p = await provider_svc.update_provider(db, pid, body.model_dump(exclude_none=False))
    if not p:
        raise HTTPException(404, "Not found")
    return _provider_out(p)


@router.post("/providers/{pid}/activate")
@needs("settings.admin")
async def activate_provider(pid: str, db: AsyncSession = Depends(get_db)):
    if not await provider_svc.set_active(db, pid):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.post("/providers/{pid}/test")
@needs("settings.admin")
async def test_provider(pid: str, db: AsyncSession = Depends(get_db)):
    p = await provider_svc.get_provider(db, pid)
    if not p:
        raise HTTPException(404, "Not found")
    return await provider_svc.test_provider(p)


@router.delete("/providers/{pid}", status_code=204)
@needs("settings.admin")
async def delete_provider(pid: str, db: AsyncSession = Depends(get_db)):
    if not await provider_svc.delete_provider(db, pid):
        raise HTTPException(404, "Not found")


# ── Build flow from AI notes ──────────────────────────────────────────────────

class BuildFlowRequest(BaseModel):
    flow_state: dict


def _spec_text(kind: str, d: dict) -> str:
    """Render the note's structured fields into a compact spec for the prompt."""
    if kind == "ai_start":
        lines = [f"connection: {d.get('connection') or '(none — file source)'}"]
        mode = d.get('source_mode') or 'sql'
        lines.append(f"source_mode: {mode}")
        if mode == "sql":
            lines.append(f"sql: {d.get('sql') or ''}")
        elif mode == "table":
            lines.append(f"table: {d.get('table') or ''}")
            lines.append(f"columns: {d.get('columns') or '*'}")
            if d.get("filter"):   lines.append(f"filter: {d['filter']}")
            if d.get("group_by"): lines.append(f"group_by: {d['group_by']}")
        else:  # file
            lines.append(f"file_path: {d.get('file_path') or ''}")
            if d.get("file_pattern"): lines.append(f"pattern: {d['file_pattern']}")
        return "\n".join(lines)
    if kind == "ai_end":
        lines = [f"connection: {d.get('connection') or '(file target)'}"]
        tmode = d.get('target_mode') or 'db'
        if tmode == "db":
            lines.append(f"table: {d.get('table') or ''}")
            lines.append(f"write_mode: {d.get('write_mode') or 'replace'}")
            if (d.get('write_mode') == 'custom') and d.get("custom_sql"):
                lines.append(f"temp_table: {d.get('temp_table') or 'tmp_sfs_load'}")
                lines.append(f"custom_sql: {d['custom_sql']}")
        else:
            lines.append(f"file_path: {d.get('file_path') or ''}")
        return "\n".join(lines)
    # ai_step
    out = f"transform: {d.get('logic') or d.get('text') or ''}"
    if d.get("output_columns"):
        out += f"\nexpected_output_columns: {d['output_columns']}"
    return out


def _parse_output_columns(code: str) -> str:
    for line in reversed(code.splitlines()):
        if "OUTPUT_COLUMNS:" in line:
            return line.split("OUTPUT_COLUMNS:", 1)[1].strip()
    return ""


@router.post("/build-flow")
async def build_flow(body: BuildFlowRequest, db: AsyncSession = Depends(get_db)):
    """
    Compile AI notes (ai_start / ai_step / ai_end) into processor code, walking
    the DAG in topological order and threading each node's declared or parsed
    output columns into the next node's generation context.
    """
    from app.services.agent_service import (
        generate_code, FLOW_SOURCE_SYSTEM_PROMPT,
        FLOW_STEP_SYSTEM_PROMPT, FLOW_SINK_SYSTEM_PROMPT,
    )
    from app.services.connection_service import list_connections

    flow = body.flow_state
    nodes = {n["id"]: n for n in flow.get("nodes", [])}
    edges = flow.get("edges", [])
    ai_kinds = {"ai_start", "ai_step", "ai_end"}
    ai_nodes = {nid: n for nid, n in nodes.items() if n.get("type") in ai_kinds}
    if not ai_nodes:
        return {"nodes": [], "error": "No AI notes found in this flow."}

    # Topological order over the whole graph (Kahn), then filter to AI notes
    from collections import defaultdict, deque
    indeg: dict = defaultdict(int)
    children: dict = defaultdict(list)
    parents: dict = defaultdict(list)
    for e in edges:
        s_, t_ = e.get("source"), e.get("target")
        if s_ in nodes and t_ in nodes:
            indeg[t_] += 1
            children[s_].append(t_)
            parents[t_].append(s_)
    q = deque([nid for nid in nodes if indeg[nid] == 0])
    order = []
    while q:
        nid = q.popleft()
        order.append(nid)
        for c in children[nid]:
            indeg[c] -= 1
            if indeg[c] == 0:
                q.append(c)

    conns = await list_connections(db)
    conn_hint = ", ".join(f"{c.name} ({c.dialect})" for c in conns) or "(none configured)"

    # Walk in topo order; carry known output-columns per node id
    known_cols: dict[str, str] = {}
    built, errors = [], []
    for nid in order:
        node = ai_nodes.get(nid)
        if not node:
            continue
        kind = node["type"]
        d = node.get("data", {})
        spec = _spec_text(kind, d)

        # Upstream context: parsed/declared columns from AI parents (or unknown)
        up_cols = [known_cols.get(p) for p in parents.get(nid, [])]
        up_cols = [c for c in up_cols if c]
        upstream_ctx = (
            f"Upstream output columns: {'; '.join(up_cols)}" if up_cols
            else "Upstream output columns: unknown — inspect df defensively."
        )

        system = (FLOW_SOURCE_SYSTEM_PROMPT if kind == "ai_start"
                  else FLOW_SINK_SYSTEM_PROMPT if kind == "ai_end"
                  else FLOW_STEP_SYSTEM_PROMPT)
        request = (
            f"Available connections: {conn_hint}\n"
            f"{upstream_ctx}\n"
            f"--- NODE SPEC ---\n{spec}"
        )
        result = await generate_code(request=request, node_type="processor",
                                     db=db, system_override=system)
        if result.get("code"):
            cols = _parse_output_columns(result["code"])
            # user-declared columns beat parsed ones (they're the contract)
            if kind == "ai_step" and d.get("output_columns"):
                cols = d["output_columns"]
            if kind == "ai_start" and d.get("source_mode") == "table" and d.get("columns") and d["columns"] != "*":
                cols = d["columns"]
            known_cols[nid] = cols
            built.append({
                "node_id": nid,
                "label": d.get("label") or {"ai_start": "Source", "ai_step": "Transform", "ai_end": "Load"}[kind],
                "code": result["code"],
                "provider": result.get("provider"),
            })
        else:
            errors.append({"node_id": nid, "error": result.get("error", "generation failed")})

    return {"nodes": built, "errors": errors}
