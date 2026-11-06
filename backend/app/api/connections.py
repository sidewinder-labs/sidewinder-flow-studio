from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services import connection_service, portability

router = APIRouter()


class ConnectionIn(BaseModel):
    name: str
    dialect: str
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None
    extra_params: dict | None = None
    browser: dict | None = None


def _out(c) -> dict:
    # Never a password or a secret option's value: names of secret options only.
    return connection_service.public(c)


@router.get("/")
async def list_connections(db: AsyncSession = Depends(get_db)):
    return [_out(c) for c in await connection_service.list_connections(db)]


@router.post("/", status_code=201)
async def create_connection(body: ConnectionIn, db: AsyncSession = Depends(get_db)):
    return _out(await connection_service.create_connection(db, body.model_dump()))


# ── Export / import (before /{cid}) ──────────────────────────────────────────

@router.get("/export")
async def export_connections(variables: bool = True, db: AsyncSession = Depends(get_db)):
    """Connection metadata (no password, no secret options) and, optionally,
    the variables. Imported connections ask for their credentials."""
    return await portability.export_connections(db, variables)


class ConnImport(BaseModel):
    bundle: dict
    # name → "skip" | "overwrite" | "rename:<new name>"; collisions not listed are skipped
    on_conflict: dict[str, str] = {}
    variables: bool = True


@router.post("/import/preview")
async def preview_import(body: ConnImport, db: AsyncSession = Depends(get_db)):
    try:
        return await portability.preview_connections(db, body.bundle)
    except portability.FormatError as exc:
        raise HTTPException(400, str(exc))


@router.post("/import")
async def import_connections(body: ConnImport, db: AsyncSession = Depends(get_db)):
    try:
        return await portability.import_connections(db, body.bundle, body.on_conflict, body.variables)
    except portability.FormatError as exc:
        raise HTTPException(400, str(exc))


class ConnectionUpdate(BaseModel):
    name: str | None = None
    dialect: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None       # blank/omitted keeps the existing password
    extra_params: dict | None = None  # secret options not sent are kept
    clear_secrets: list[str] | None = None
    browser: dict | None = None


@router.put("/{cid}")
async def update_connection(cid: str, body: ConnectionUpdate, db: AsyncSession = Depends(get_db)):
    c = await connection_service.update_connection(db, cid, body.model_dump(exclude_unset=True))
    if not c:
        raise HTTPException(404, "Not found")
    return _out(c)


@router.delete("/{cid}", status_code=204)
async def delete_connection(cid: str, db: AsyncSession = Depends(get_db)):
    if not await connection_service.delete_connection(db, cid):
        raise HTTPException(404, "Not found")


@router.post("/{cid}/test")
async def test_connection(cid: str, db: AsyncSession = Depends(get_db)):
    c = await connection_service.get_connection(db, cid)
    if not c:
        raise HTTPException(404, "Not found")
    return await connection_service.test_connection(c)
