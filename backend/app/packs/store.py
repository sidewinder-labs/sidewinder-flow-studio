"""Installed plugin packs: install, enable, disable, roll back, remove, and
load at startup.

Layout, under the data folder:

    plugins/<id>/<version>/{manifest.json, signature.json, site/}
    plugins/state.json      {"packs": {id: {"version", "previous", "enabled", "error", "notice"}}}
    plugins/loading.json    written while a pack's register() runs; present at
                            startup means SFS died loading it, so it is disabled
    plugins/safe_mode       if present (or SFS_SAFE_MODE=1), no plugin loads

Every start re-verifies each enabled pack's signature and files, and its
compatibility with this SFS (after an app update an incompatible pack is
disabled with a notice). A pack that fails to load is disabled and shown as
failed: a plugin never stops SFS from starting.
"""
from __future__ import annotations

import importlib
import importlib.machinery
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.packs.fmt import PackError, compatible, current_abi, current_platform, read_signed_dir, version_tuple

log = logging.getLogger("sfs.packs")


def data_dir() -> Path:
    from app.core.config import settings
    return Path(settings.PROJECTS_DIR).resolve().parent


def folder() -> Path:
    """The packs folder, not created (reading must not leave folders behind)."""
    return data_dir() / "plugins"


def root() -> Path:
    """The packs folder, created: for writing."""
    p = folder()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── state ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    p = folder() / "state.json"
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        return s if isinstance(s.get("packs"), dict) else {"packs": {}}
    except (OSError, ValueError):
        return {"packs": {}}


def save_state(s: dict) -> None:
    p = root() / "state.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def safe_mode() -> bool:
    return os.environ.get("SFS_SAFE_MODE", "").strip().lower() in ("1", "true", "yes") or (folder() / "safe_mode").exists()


def set_safe_mode(on: bool) -> None:
    f = (root() if on else folder()) / "safe_mode"
    if on:
        f.write_text("Remove this file (or turn safe mode off in Plugins) to load plugins again.\n")
    else:
        f.unlink(missing_ok=True)


# ── what the app already provides ────────────────────────────────────────────

def _norm(n: str) -> str:
    import re
    return re.sub(r"[-_.]+", "-", n).lower()


def provided_distributions() -> dict[str, str]:
    """Distributions this app ships: the list exported at build time when there
    is one (the frozen desktop app has no metadata for most of them), else the
    running environment's."""
    candidates = [os.environ.get("SFS_BUNDLED_DISTRIBUTIONS", "")]
    if getattr(sys, "frozen", False):
        candidates.append(str(Path(getattr(sys, "_MEIPASS", "")) / "bundled-distributions.json"))
    for c in filter(None, candidates):
        try:
            return {_norm(k): v for k, v in json.loads(Path(c).read_text())["distributions"].items()}
        except (OSError, ValueError, KeyError):
            continue
    from importlib import metadata
    packs = str(folder().resolve())
    app_paths = [p for p in sys.path if not str(Path(p or ".").resolve()).startswith(packs)]   # not loaded packs
    return {_norm(d.metadata["Name"]): d.version for d in metadata.distributions(path=app_paths) if d.metadata["Name"]}


def site_distributions(site: Path) -> dict[str, str]:
    out = {}
    for d in Path(site).glob("*.dist-info"):
        name, _, ver = d.name[:-len(".dist-info")].rpartition("-")
        out[_norm(name)] = ver
    return out


# ── install, update, roll back, remove ───────────────────────────────────────

def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if dest not in target.parents and target != dest:
            raise PackError(f"it contains a path outside the pack ({member.filename})")
    zf.extractall(dest)


def install_zip(zip_path: Path, sfs_version: str, *, keys=None, provided: dict | None = None) -> dict:
    """Verify a pack and install it as the active version. Returns the manifest.
    The previously active version is kept for roll back; older ones are removed."""
    tmp = Path(tempfile.mkdtemp(prefix="install-", dir=root()))
    try:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                _safe_extract(zf, tmp)
        except zipfile.BadZipFile:
            raise PackError("it isn't a zip file") from None
        m = read_signed_dir(tmp, keys=keys)
        why = compatible(m, sfs_version)
        if why:
            raise PackError(f"{m['name']} {m['version']} doesn't fit this SFS: {why}")
        app_has = set(provided if provided is not None else provided_distributions()) - {_norm(m["name"])}
        clash = sorted(set(site_distributions(tmp / "site")) & app_has)
        if clash:
            raise PackError(f"it contains {', '.join(clash)}, which SFS itself provides; packs must use the app's")
        dest = root() / m["id"] / m["version"]
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    s = load_state()
    cur = s["packs"].get(m["id"], {})
    previous = cur.get("version") if cur.get("version") != m["version"] else cur.get("previous")
    s["packs"][m["id"]] = {"version": m["version"], "previous": previous, "enabled": True, "error": None,
                           "notice": None, "installed_at": _now()}
    save_state(s)
    _prune(m["id"], {m["version"], previous})
    return m


def _prune(pid: str, keep: set) -> None:
    for d in (root() / pid).iterdir() if (root() / pid).exists() else []:
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)


def rollback(pid: str) -> str:
    s = load_state()
    cur = s["packs"].get(pid)
    if not cur or not cur.get("previous") or not (root() / pid / cur["previous"]).exists():
        raise PackError("there is no previous version to roll back to")
    cur["version"], cur["previous"] = cur["previous"], cur["version"]
    cur.update(enabled=True, error=None, notice=None)
    save_state(s)
    return cur["version"]


def set_enabled(pid: str, enabled: bool) -> None:
    s = load_state()
    if pid not in s["packs"]:
        raise PackError(f"'{pid}' isn't installed")
    s["packs"][pid].update(enabled=enabled, **({"error": None, "notice": None} if enabled else {}))
    save_state(s)


def remove(pid: str) -> None:
    s = load_state()
    s["packs"].pop(pid, None)
    save_state(s)
    shutil.rmtree(root() / pid, ignore_errors=True)


def installed() -> list[dict]:
    """Installed packs with their manifests and status (no file re-check: that is startup's job)."""
    out = []
    for pid, st in sorted(load_state()["packs"].items()):
        d = folder() / pid / str(st.get("version"))
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            m = {"id": pid, "name": pid, "version": st.get("version")}
        out.append({**{k: m.get(k) for k in ("id", "name", "version", "edition", "description", "sfs_min", "sfs_max")},
                    "enabled": bool(st.get("enabled")), "error": st.get("error"), "notice": st.get("notice"),
                    "previous": st.get("previous"), "loaded": pid in LOADED})
    return out


# ── loading at startup ───────────────────────────────────────────────────────

LOADED: dict[str, dict] = {}         # id → manifest, for packs loaded in this process


class _PackFinder:
    """Resolves a pack's top-level packages from its site/ first. Needed in the
    frozen desktop app, whose importer comes before sys.path: without this a
    pack overriding a bundled plugin would still import the bundled copy."""

    def __init__(self, site: Path, names: set[str]):
        self.site, self.names = str(site), names

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] not in self.names:
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, path if "." in fullname else [self.site])


def _top_level(site: Path) -> set[str]:
    names = set()
    for p in site.iterdir():
        if p.suffix in (".dist-info", ".egg-info", ".pth") or p.name.startswith("_") and p.name != "__init__.py":
            continue
        if p.is_dir() and (p / "__init__.py").exists():
            names.add(p.name)
        elif p.suffix in (".py", ".pyd", ".so"):
            names.add(p.name.split(".", 1)[0])
    return names


def _activate(site: Path) -> None:
    sys.path.insert(0, str(site))
    sys.meta_path.insert(0, _PackFinder(site, _top_level(site)))


def load_all(make_api, sfs_version: str, bundled: dict[str, str] | None = None, *, keys=None) -> set[str]:
    """Load every enabled, valid, compatible pack. `bundled` maps entry-point
    plugin names to their installed versions: a pack with the same id loads
    instead of the bundled plugin only if it is newer. Returns the ids loaded,
    so the bundled entry points they replace are skipped."""
    if safe_mode():
        log.warning("Safe mode: no plugins loaded")
        return set()
    bundled = bundled or {}
    if not folder().exists():
        return set()
    s = load_state()
    marker = folder() / "loading.json"
    if marker.exists():                                  # the last start died while loading this one
        try:
            pid = json.loads(marker.read_text()).get("id")
        except ValueError:
            pid = None
        if pid in s["packs"]:
            s["packs"][pid].update(enabled=False, error="SFS stopped while loading this plugin; it was disabled.")
        marker.unlink(missing_ok=True)
        save_state(s)
    loaded = set()
    for pid, st in sorted(s["packs"].items()):
        if not st.get("enabled"):
            continue
        pack_dir = folder() / pid / str(st.get("version"))
        try:
            m = read_signed_dir(pack_dir, keys=keys)
            why = compatible(m, sfs_version)
            if why:
                st.update(enabled=False, notice=f"Disabled after the update to SFS {sfs_version}: {why}. "
                                                "Check Plugins → Updates for a compatible version.")
                continue
            if pid in bundled and version_tuple(m["version"]) <= version_tuple(bundled[pid]):
                st["notice"] = f"The bundled version ({bundled[pid]}) is as new or newer, so it is used instead."
                continue
            _activate(pack_dir / "site")
            marker.write_text(json.dumps({"id": pid, "at": _now()}))
            module, func = m["register"].split(":", 1)
            hook = getattr(importlib.import_module(module), func)
            hook(make_api(f"pack:{pid}"))
            LOADED[pid] = m
            loaded.add(pid)
            st.update(error=None, notice=None)
            log.info("Loaded plugin pack %s %s", pid, m["version"])
        except Exception as exc:                          # bad signature, import error, failing register()
            st.update(enabled=False, error=f"{type(exc).__name__}: {exc}"[:500])
            log.error("Plugin pack %s failed to load and was disabled: %s", pid, exc)
        finally:
            marker.unlink(missing_ok=True)
    save_state(s)
    return loaded


def platform_info() -> dict:
    return {"platform": current_platform(), "python_abi": current_abi()}
