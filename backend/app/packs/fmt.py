"""The pack format, its hashes and signatures. Shared by the app (verify) and
the pack tools (build, sign).

A pack is a zip:

    manifest.json    id, name, version, edition, description, sfs_min, sfs_max,
                     platform (flavour-os-arch, e.g. desktop-windows-x64), python_abi, register ("module:function"),
                     dependencies [{name, version}], sha256 (of site/), licence_feature?
    signature.json   {"kid": "...", "signature": base64}: Ed25519 over the exact
                     bytes of manifest.json
    site/            the plugin package and its own dependencies, importable as-is

The manifest's `sha256` is a hash over every file under site/ (path and
content, sorted), so the signature covers the whole pack, and an installed
pack can be re-checked on disk at every start.

The catalog is signed the same way: catalog.json = {"kid", "payload": base64
of the catalog JSON, "signature": over those payload bytes}.
"""
from __future__ import annotations

import base64
import hashlib
import json
import platform as _platform
import re
import sys
from pathlib import Path

EDITIONS = ("community", "enterprise")
REQUIRED = ("id", "name", "version", "edition", "sfs_min", "sfs_max", "platform", "python_abi", "register", "sha256")


class PackError(ValueError):
    """A pack or catalog that must not be installed or trusted."""


# ── where we run ─────────────────────────────────────────────────────────────

def flavour() -> str:
    """desktop (the frozen app) or server (Docker, and source checkouts): they
    bundle different libraries, so packs are built for each."""
    import os
    return "desktop" if os.environ.get("SFS_INSTALL", "").lower() == "desktop" else "server"


def current_platform() -> str:
    """e.g. desktop-windows-x64, server-linux-x64, server-linux-arm64."""
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
    m = _platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(m, m)
    return f"{flavour()}-{system}-{arch}"


def current_abi() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def version_tuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", str(v))[:4])


def compatible(manifest: dict, sfs_version: str, *, platform: str | None = None, abi: str | None = None) -> str | None:
    """None if the pack fits this app; else why not."""
    platform, abi = platform or current_platform(), abi or current_abi()
    if manifest.get("platform") not in (platform, "any"):
        return f"built for {manifest.get('platform')}, this is {platform}"
    if manifest.get("python_abi") not in (abi, "any"):
        return f"built for Python {manifest.get('python_abi')}, this app runs {abi}"
    v = version_tuple(sfs_version)
    if v < version_tuple(manifest["sfs_min"]):
        return f"needs SFS {manifest['sfs_min']} or newer"
    if v > version_tuple(manifest["sfs_max"]) and not _same_prefix(sfs_version, manifest["sfs_max"]):
        return f"supports SFS up to {manifest['sfs_max']}"
    return None


def _same_prefix(v: str, max_: str) -> bool:
    """'1.39.2' fits sfs_max '1.39' (a patch release of a supported version)."""
    m = version_tuple(max_)
    return len(str(max_).split(".")) == 2 and version_tuple(v)[:2] == m[:2]


# ── hashes ───────────────────────────────────────────────────────────────────

def tree_sha256(root: Path) -> str:
    """Hash of every file under root: relative path and content, in sorted order."""
    h = hashlib.sha256()
    root = Path(root)
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in rel.split("/"):
            continue                                   # written by Python when the pack is imported
        h.update(rel.encode() + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── signatures ───────────────────────────────────────────────────────────────

def _keys(keys):
    if keys is not None:
        return keys
    from app.packs.keys import PLUGIN_KEYS
    return PLUGIN_KEYS


def verify_bytes(data: bytes, kid: str, signature_b64: str, *, keys=None) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pub = _keys(keys).get(kid or "")
    if not pub or pub == "REPLACED_BY_KEYGEN":
        raise PackError(f"signed with a key this SFS doesn't know ({kid or 'none'})")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(base64.b64decode(signature_b64), data)
    except (InvalidSignature, ValueError):
        raise PackError("its signature is invalid: it was changed after signing, or isn't official") from None


def sign_bytes(data: bytes, private_key_pem: bytes) -> str:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return base64.b64encode(load_pem_private_key(private_key_pem, password=None).sign(data)).decode()


def check_manifest(m: dict) -> dict:
    missing = [k for k in REQUIRED if not m.get(k)]
    if missing:
        raise PackError(f"its manifest lacks {', '.join(missing)}")
    if m["edition"] not in EDITIONS:
        raise PackError(f"unknown edition '{m['edition']}'")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]{0,63}", m["id"]):
        raise PackError(f"bad plugin id '{m['id']}'")
    if ":" not in m["register"]:
        raise PackError("its register entry must read module:function")
    return m


def read_signed_dir(folder: Path, *, keys=None, check_files: bool = True) -> dict:
    """Verify an unpacked pack (manifest.json, signature.json, site/). Returns the manifest."""
    folder = Path(folder)
    try:
        raw = (folder / "manifest.json").read_bytes()
        sig = json.loads((folder / "signature.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PackError("it has no readable manifest.json and signature.json") from None
    verify_bytes(raw, sig.get("kid"), sig.get("signature", ""), keys=keys)
    m = check_manifest(json.loads(raw))
    if check_files and tree_sha256(folder / "site") != m["sha256"]:
        raise PackError("its files differ from what was signed (changed on disk?)")
    return m


def signed_catalog(doc: dict, *, keys=None) -> dict:
    """Verify catalog.json ({kid, payload, signature}) and return the catalog."""
    if not isinstance(doc, dict) or "payload" not in doc:
        raise PackError("the catalog isn't signed")
    payload = base64.b64decode(doc["payload"])
    verify_bytes(payload, doc.get("kid"), doc.get("signature", ""), keys=keys)
    cat = json.loads(payload)
    if cat.get("format") != "sfs-catalog" or not cat.get("generated_at"):
        raise PackError("this isn't an SFS plugin catalog")
    return cat
