"""The official plugin catalog: fetch, verify, offer what fits this app, download.

    GET <SFS_CATALOG_URL>/catalog.json                        signed index
    GET <SFS_CATALOG_URL>/packs/<id>/<version>/<platform>.zip  a pack

Catalog (inside the signed payload):

    {"format": "sfs-catalog", "generated_at": "2026-11-07T12:00:00Z",
     "plugins": [{"id", "name", "description", "edition", "licence_feature"?,
                  "versions": [{"version", "sfs_min", "sfs_max",
                                "packs": [{"platform", "python_abi", "path", "sha256", "size"}]}]}]}

A catalog older than the newest one seen is rejected, so an old catalog can't
be replayed to push versions since withdrawn. The last good catalog is cached,
so the Plugins menu works offline with what it knew.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.packs import store
from app.packs.fmt import PackError, compatible, current_abi, current_platform, file_sha256, signed_catalog, version_tuple

DEFAULT_URL = "https://plugins.sidewinderlabs.dev"


def url() -> str:
    return os.environ.get("SFS_CATALOG_URL", DEFAULT_URL).rstrip("/")


def _seen_path() -> Path:
    return store.folder() / "catalog_seen.json"


def _cache_path() -> Path:
    return store.folder() / "catalog.json"


def accept(doc: dict, *, keys=None) -> dict:
    """Verify a fetched catalog, refuse replays, cache it. Returns the catalog."""
    cat = signed_catalog(doc, keys=keys)
    try:
        seen = json.loads(_seen_path().read_text()).get("generated_at", "")
    except (OSError, ValueError):
        seen = ""
    if seen and cat["generated_at"] < seen:
        raise PackError(f"the catalog is older ({cat['generated_at']}) than one already seen ({seen}): "
                        "refusing it, as an old catalog could offer withdrawn versions")
    store.root()
    _cache_path().write_text(json.dumps(doc), encoding="utf-8")
    _seen_path().write_text(json.dumps({"generated_at": cat["generated_at"]}), encoding="utf-8")
    return cat


def fetch(*, http=None, keys=None) -> dict:
    import httpx
    client = http or httpx.Client(timeout=20, follow_redirects=True)
    try:
        r = client.get(url() + "/catalog.json")
        r.raise_for_status()
        doc = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PackError(f"the catalog at {url()} couldn't be read: {exc}") from None
    return accept(doc, keys=keys)


def cached(*, keys=None) -> dict | None:
    try:
        return signed_catalog(json.loads(_cache_path().read_text(encoding="utf-8")), keys=keys)
    except (OSError, ValueError, PackError):
        return None


def _pack_for(version: dict, platform: str, abi: str) -> dict | None:
    for p in version.get("packs") or []:
        if p.get("platform") in (platform, "any") and p.get("python_abi") in (abi, "any"):
            return p
    return None


def offers(cat: dict | None, sfs_version: str, *, platform=None, abi=None) -> list[dict]:
    """Each catalog plugin with its compatible versions, newest first (only
    versions with a pack for this platform and Python, and sfs range that fits)."""
    platform, abi = platform or current_platform(), abi or current_abi()
    out = []
    for p in (cat or {}).get("plugins") or []:
        fits = []
        for v in p.get("versions") or []:
            pack = _pack_for(v, platform, abi)
            if pack and not compatible({**v, "platform": pack["platform"], "python_abi": pack["python_abi"]},
                                       sfs_version, platform=platform, abi=abi):
                fits.append(v["version"])
        fits.sort(key=version_tuple, reverse=True)
        out.append({k: p.get(k) for k in ("id", "name", "description", "edition", "licence_feature")} | {"versions": fits})
    return out


# ── licences (verified by the enterprise package and the catalog server) ─────

LICENCE_FILE = "licence.key"


def licence_text() -> str | None:
    if os.environ.get("SFS_LICENSE", "").strip():
        return os.environ["SFS_LICENSE"].strip()
    f = os.environ.get("SFS_LICENSE_FILE", "").strip()
    p = Path(f).expanduser() if f else store.data_dir() / LICENCE_FILE
    try:
        return "".join(p.read_text(encoding="utf-8").split()) or None
    except OSError:
        return None


def licence_summary() -> dict | None:
    """Licensee, expiry and features. Verified when SFS Enterprise is installed;
    otherwise only decoded (the catalog server and the plugins verify it)."""
    text = licence_text()
    if not text:
        return None
    try:
        from sfs_enterprise import licensing
        lic = licensing.parse(text)
        return {"licensee": lic.licensee, "expires": lic.expires, "features": lic.features,
                "evaluation": lic.evaluation, "verified": True, "expired": lic.expired()}
    except ImportError:
        pass
    except Exception as exc:
        return {"error": str(exc), "verified": False}
    import base64
    try:
        payload = text.split(".")[1]
        d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return {"licensee": d.get("licensee"), "expires": d.get("expires"), "features": d.get("features", []),
                "evaluation": bool(d.get("evaluation")), "verified": False}
    except Exception:
        return {"error": "This doesn't look like an SFS licence key.", "verified": False}


def save_licence(text: str) -> dict:
    text = "".join((text or "").split())
    if not text.startswith("SFS-LICENSE-1.") or text.count(".") < 2:
        raise PackError("This isn't an SFS licence key (it starts with SFS-LICENSE-1.)")
    p = store.data_dir() / LICENCE_FILE
    p.write_text(text + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    s = licence_summary() or {}
    if s.get("error"):
        p.unlink(missing_ok=True)
        raise PackError(s["error"])
    return s


# ── download and install ─────────────────────────────────────────────────────

def download_and_install(plugin_id: str, version: str | None, sfs_version: str, *, http=None, keys=None) -> dict:
    import httpx
    cat = cached(keys=keys)
    if cat is None:
        raise PackError("no catalog yet: refresh the catalog first (needs a connection)")
    plugin = next((p for p in cat.get("plugins") or [] if p.get("id") == plugin_id), None)
    offer = next((o for o in offers(cat, sfs_version) if o["id"] == plugin_id), None)
    if plugin is None or not offer or not offer["versions"]:
        raise PackError(f"'{plugin_id}' has no version for this SFS ({sfs_version}, {current_platform()})")
    version = version or offer["versions"][0]
    if version not in offer["versions"]:
        raise PackError(f"{plugin_id} {version} doesn't fit this SFS")
    v = next(x for x in plugin["versions"] if x["version"] == version)
    pack = _pack_for(v, current_platform(), current_abi())
    headers = {}
    if plugin.get("edition") == "enterprise":
        text = licence_text()
        if not text:
            raise PackError(f"{plugin.get('name', plugin_id)} is an Enterprise plugin: add your licence key first")
        headers["Authorization"] = f"Licence {text}"
    client = http or httpx.Client(timeout=120, follow_redirects=True)
    r = client.get(f"{url()}/{pack['path'].lstrip('/')}", headers=headers)
    if r.status_code in (401, 403):
        try:
            reason = r.json().get("error")
        except ValueError:
            reason = r.text[:200]
        raise PackError(f"the catalog refused the download: {reason}")
    if r.status_code >= 400:
        raise PackError(f"the download failed (HTTP {r.status_code})")
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=store.root())
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(r.content)
        if file_sha256(Path(tmp)) != pack["sha256"]:
            raise PackError("the downloaded file doesn't match the catalog's checksum")
        return store.install_zip(Path(tmp), sfs_version, keys=keys)
    finally:
        Path(tmp).unlink(missing_ok=True)
