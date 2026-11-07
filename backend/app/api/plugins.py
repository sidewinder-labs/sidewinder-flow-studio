"""Plugins menu API: bundled plugins, installed packs, the catalog, licence, safe mode.

Changes take effect when SFS restarts: plugins register at startup, and a
running flow must not have its engines swapped underneath it.
"""
import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.packs import catalog, store
from app.packs.fmt import PackError, version_tuple

router = APIRouter()
_restart_needed: list[str] = []


def _changed(what: str):
    if what not in _restart_needed:
        _restart_needed.append(what)


def _view(sfs_version: str, *, catalog_error: str | None = None) -> dict:
    from app.api.diagnostics import install_type
    from app.engine.registry import loaded_plugins
    cat = catalog.cached()
    offers = catalog.offers(cat, sfs_version)
    installed = store.installed()
    bundled = [p for p in loaded_plugins() if not p.get("replaced_by_pack")]
    have = {p["id"]: p["version"] for p in installed} | {b["name"]: b.get("version") or "0" for b in bundled
                                                          if b["name"] not in {p["id"] for p in installed}}
    available = [o for o in offers if o["versions"] and o["id"] not in have]
    updates = [o | {"installed": have[o["id"]]} for o in offers
               if o["versions"] and o["id"] in have and version_tuple(o["versions"][0]) > version_tuple(have[o["id"]])]
    return {
        "sfs_version": sfs_version, **store.platform_info(), "install": install_type(),
        "safe_mode": store.safe_mode(), "restart_needed": list(_restart_needed),
        "bundled": [{"id": b["name"], "version": b.get("version"), "loaded": b.get("loaded"), "error": b.get("error")}
                    for b in bundled],
        "installed": installed, "available": available, "updates": updates,
        "catalog": {"url": catalog.url(), "generated_at": (cat or {}).get("generated_at"), "error": catalog_error},
        "licence": catalog.licence_summary(),
        "can_restart": install_type() in ("docker", "desktop"),
    }


def _fail(exc: Exception):
    raise HTTPException(400, str(exc))


@router.get("/")
def get_plugins(request: Request):
    return _view(request.app.version)


@router.post("/catalog/refresh")
async def refresh_catalog(request: Request):
    try:
        await run_in_threadpool(catalog.fetch)
        return _view(request.app.version)
    except PackError as exc:                      # offline, or refused: show what's cached, and why
        return _view(request.app.version, catalog_error=str(exc))


class InstallIn(BaseModel):
    id: str
    version: str | None = None


@router.post("/install")
async def install(body: InstallIn, request: Request):
    try:
        m = await run_in_threadpool(catalog.download_and_install, body.id, body.version, request.app.version)
    except PackError as exc:
        _fail(exc)
    _changed(f"install {m['id']} {m['version']}")
    return _view(request.app.version)


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    """Install a signed pack from a file (no connection needed). Same checks as the catalog."""
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=store.root())
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(await file.read())
        m = await run_in_threadpool(store.install_zip, Path(tmp), request.app.version)
    except PackError as exc:
        _fail(exc)
    finally:
        Path(tmp).unlink(missing_ok=True)
    _changed(f"install {m['id']} {m['version']}")
    return _view(request.app.version)


@router.post("/{pid}/{action}")
def act(pid: str, action: str, request: Request):
    try:
        if action == "enable":
            store.set_enabled(pid, True)
        elif action == "disable":
            store.set_enabled(pid, False)
        elif action == "rollback":
            store.rollback(pid)
        elif action == "remove":
            store.remove(pid)
        else:
            raise HTTPException(404, f"Unknown action '{action}'")
    except PackError as exc:
        _fail(exc)
    _changed(f"{action} {pid}")
    return _view(request.app.version)


class SafeModeIn(BaseModel):
    on: bool


@router.put("/safe-mode")
def safe_mode(body: SafeModeIn, request: Request):
    store.set_safe_mode(body.on)
    _changed("safe mode " + ("on" if body.on else "off"))
    return _view(request.app.version)


class LicenceIn(BaseModel):
    text: str


@router.put("/licence")
def put_licence(body: LicenceIn, request: Request):
    try:
        catalog.save_licence(body.text)
    except PackError as exc:
        _fail(exc)
    _changed("licence")
    return _view(request.app.version)


@router.delete("/licence")
def delete_licence(request: Request):
    (store.data_dir() / catalog.LICENCE_FILE).unlink(missing_ok=True)
    _changed("licence")
    return _view(request.app.version)


@router.post("/restart")
async def restart():
    """Docker and desktop only: exit, so the container restarts (restart:
    unless-stopped) or the desktop app offers Restart."""
    from app.api.diagnostics import install_type
    if install_type() not in ("docker", "desktop"):
        raise HTTPException(400, "Restart SFS yourself: this install has nothing to start it again.")

    async def later():
        await asyncio.sleep(0.5)
        os._exit(3)
    asyncio.get_running_loop().create_task(later())
    return {"restarting": True}
