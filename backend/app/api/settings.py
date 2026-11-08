"""Meta → Settings: view and change the settings in app.core.app_settings."""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import app_settings, restart as _restart

router = APIRouter()


class SettingsIn(BaseModel):
    values: dict[str, Any]          # key → new value; null or "" resets it to the default


@router.get("/")
def get_settings():
    return app_settings.view()


@router.put("/")
def save_settings(body: SettingsIn):
    try:
        app_settings.update(body.values)
    except ValueError as e:
        detail = e.args[0] if e.args and isinstance(e.args[0], dict) else {"_": str(e)}
        raise HTTPException(400, {"errors": detail})
    return app_settings.view()


@router.post("/restart")
async def restart():
    if not _restart.can_restart():
        raise HTTPException(400, "Restart SFS yourself: this install has nothing to start it again.")
    _restart.schedule()
    return {"restarting": True}
