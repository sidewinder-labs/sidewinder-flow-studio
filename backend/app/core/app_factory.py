import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pathlib import Path

from app.core.config import settings
from app.core.database import init_db
from app.scheduler.scheduler import start_scheduler, stop_scheduler
from app.api import projects, connections, execute, metadata, ws, agent, runs, custom_processors, capabilities, explore


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure all data directories exist before anything writes to them.
    # Makes startup robust regardless of Docker/local/env-var overrides.
    from pathlib import Path as _P
    for _p in [
        settings.PROJECTS_DIR,
        settings.FRAME_CACHE_DIR,
        settings.REPORTS_DIR,
        str(_P(settings.PERSIST_DB).parent),
    ]:
        try:
            _P(_p).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    # Discover third-party and enterprise plugins (entry point group
    # "sfs.plugins"). A failing plugin is logged and skipped, never fatal.
    try:
        from app.engine import executor as _executor  # registers builtin engines
        from app.engine.registry import load_plugins
        load_plugins()
    except Exception as _exc:
        import logging; logging.getLogger("sfs").error("Plugin load failed: %s", _exc)

    await init_db()
    start_scheduler()
    yield
    stop_scheduler()


HEALTH_PATH = "/api/health"


class _DropPath:
    """Logging filter: drop uvicorn access lines for one path."""
    def __init__(self, path: str):
        self.needle = f'"GET {path} '

    def filter(self, record) -> bool:
        return self.needle not in record.getMessage()


def _quiet_access_log(path: str) -> None:
    """Container healthchecks call the health endpoint every few seconds; an
    access line for each would bury everything else in `docker logs`."""
    import logging
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _DropPath) for f in access.filters):
        access.addFilter(_DropPath(path))


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sidewinder Flow Studio API",
        version="1.32",
        description="Visual Python notebook DAG with scheduling",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects.router,    prefix="/api/projects",    tags=["projects"])
    app.include_router(connections.router, prefix="/api/connections", tags=["connections"])
    app.include_router(execute.router,     prefix="/api/execute",     tags=["execute"])
    app.include_router(metadata.router,    prefix="/api/metadata",    tags=["metadata"])
    app.include_router(agent.router,       prefix="/api/agent",       tags=["agent"])
    app.include_router(runs.router,        prefix="/api/runs",        tags=["runs"])
    app.include_router(custom_processors.router, prefix="/api/custom-processors", tags=["custom"])
    app.include_router(capabilities.router, prefix="/api/capabilities", tags=["capabilities"])
    app.include_router(explore.router,     prefix="/api/explore",     tags=["explore"])
    app.include_router(ws.router,          prefix="/ws",              tags=["websocket"])
    @app.get(HEALTH_PATH, include_in_schema=False)
    async def health():
        return {"status": "ok"}
    _quiet_access_log(HEALTH_PATH)

    if os.getenv("SFS_DESKTOP") == "1":
        from app.api import desktop
        app.include_router(desktop.router, prefix="/api/desktop",     tags=["desktop"])

    # Single-container and desktop mode: serve the built React app. The
    # default is backend/static (next to main.py); STATIC_DIR overrides it.
    static_dir = Path(os.getenv("STATIC_DIR") or Path(__file__).resolve().parents[2] / "static")
    if os.getenv("SERVE_STATIC", "false").lower() == "true" and static_dir.exists():
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            # Client-side routes get the app; unknown API paths stay 404s
            if full_path.startswith(("api/", "ws/")):
                raise HTTPException(404, "Not found")
            return FileResponse(str(static_dir / "index.html"))

    return app
