"""Diagnostics for bug reports: what this SFS is and how it was installed.

    GET /api/diagnostics  → {version, install, os, python, plugins, packages, text}

Only versions and names: no connection details, paths, hostnames or secrets.
Plugin load errors are reduced to their exception type, since messages can
carry paths. `text` is the same, ready to paste into an issue.
"""
import os
import platform
from importlib import metadata

from fastapi import APIRouter, Request

router = APIRouter()

# The Docker images set SFS_INSTALL=docker and the desktop launcher sets
# desktop; anything else is a source install, which isn't supported for use.
INSTALL_TYPES = {"docker": "Docker", "desktop": "Desktop"}
KEY_PACKAGES = ("pandas", "pyarrow", "duckdb", "polars", "sqlalchemy", "fastapi", "connectorx", "sqlglot", "ibis-framework")


def install_type() -> str:
    v = os.environ.get("SFS_INSTALL", "").strip().lower()
    return v if v in INSTALL_TYPES else "source"


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def collect(sfs_version: str) -> dict:
    from app.engine.registry import load_plugins
    plugins = []
    for p in load_plugins():
        err = p.get("error")
        plugins.append({"name": p["name"], "version": p.get("version"),
                        "status": "loaded" if p.get("loaded") else "failed",
                        **({"error_type": err.split(":", 1)[0][:80]} if err and not p.get("loaded") else {})})
    d = {
        "version": sfs_version,
        "install": install_type(),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "python": platform.python_version(),
        "plugins": plugins,
        "packages": {k: _version(k) for k in KEY_PACKAGES},
    }
    d["text"] = as_text(d)
    return d


def as_text(d: dict) -> str:
    install = INSTALL_TYPES.get(d["install"], "Built from source (unsupported)")
    lines = [f"SFS {d['version']}", f"Install: {install}", f"OS: {d['os']} ({d['arch']})",
             f"Python: {d['python']}"]
    lines.append("Plugins: " + (", ".join(
        f"{p['name']} {p['version'] or '?'} ({p['status']}{', ' + p['error_type'] if p.get('error_type') else ''})"
        for p in d["plugins"]) or "none"))
    lines.append("Packages: " + ", ".join(f"{k} {v}" for k, v in d["packages"].items() if v))
    return "\n".join(lines)


@router.get("/")
def diagnostics(request: Request):
    return collect(request.app.version)
