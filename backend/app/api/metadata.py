import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.core.database import get_db
from app.models.variable import Variable

router = APIRouter()


class VarIn(BaseModel):
    scope: str = "global"
    key: str
    value: str | None = None
    description: str | None = None


@router.get("/variables")
async def list_vars(scope: str = "global", db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Variable).where(Variable.scope == scope).order_by(Variable.key))
    return [{"id": v.id, "scope": v.scope, "key": v.key, "value": v.value, "description": v.description}
            for v in r.scalars().all()]


@router.post("/variables", status_code=201)
async def create_var(body: VarIn, db: AsyncSession = Depends(get_db)):
    v = Variable(id=str(uuid.uuid4()), **body.model_dump())
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return {"id": v.id, "scope": v.scope, "key": v.key, "value": v.value, "description": v.description}


@router.delete("/variables/{vid}", status_code=204)
async def delete_var(vid: str, db: AsyncSession = Depends(get_db)):
    r = await db.execute(delete(Variable).where(Variable.id == vid))
    await db.commit()
    if r.rowcount == 0:
        raise HTTPException(404, "Not found")


@router.get("/frame-columns")
async def frame_columns(project_id: str, node_id: str):
    """Column names (and dtypes) of a node's cached output frame.

    Used by engine params of type "column" so the node panel can offer a real
    dropdown instead of a free-text box. Returns an empty list if the node has
    not run yet — callers should degrade to a text input.
    """
    from app.core.frame_cache import has_frame, load_frame_head
    if not has_frame(project_id, node_id):
        return {"columns": [], "ran": False}
    try:
        df = load_frame_head(project_id, node_id, 1)      # the dtypes, not the whole frame
        return {
            "ran": True,
            "columns": [{"name": str(c), "dtype": str(df[c].dtype)} for c in df.columns],
        }
    except Exception as exc:
        return {"columns": [], "ran": False, "error": str(exc)}
