"""Restarting SFS from inside the app (Plugins and Settings → Restart now).

Only where something starts SFS again: Docker (restart: unless-stopped) and
the desktop app (it offers Restart when the backend exits with code 3).
"""
import asyncio
import os

EXIT_CODE = 3


def can_restart() -> bool:
    from app.api.diagnostics import install_type
    return install_type() in ("docker", "desktop")


def schedule() -> None:
    """Exit shortly, after the HTTP response has gone out."""
    async def later():
        await asyncio.sleep(0.5)
        os._exit(EXIT_CODE)
    asyncio.get_running_loop().create_task(later())
