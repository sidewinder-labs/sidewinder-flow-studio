"""Pack tools for the release pipeline (and for trying packs locally).

    python -m app.packs.tools bundled  --out bundled.json              # what this app build provides
    python -m app.packs.tools build    <plugin dir> --bundled bundled.json --platform server-linux-x64 \\
                                       --sfs 1.39 [--sfs-max 1.39] [--edition community] [--key k.pem --kid 2026-11] --out dist/
    python -m app.packs.tools catalog  dist/**/*.zip --previous catalog.json --key k.pem --kid 2026-11 --out catalog.json
    python -m app.packs.tools keygen   --out ~/.config/sidewinder-labs/keys/plugins-2026-11.pem

Run from backend/ (the tools import app.packs.fmt). The builder never puts a
distribution the app provides into a pack: it resolves the plugin's
dependencies pinned to the app's versions, drops what the app has, and
refuses the pack if any remains.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.packs.fmt import PackError, check_manifest, current_abi, sign_bytes, tree_sha256

# Tools in the build environment that the app doesn't ship (PyInstaller excludes them)
NOT_BUNDLED = {"pyinstaller", "pyinstaller-hooks-contrib", "pytest", "pluggy", "iniconfig", "pip", "setuptools",
               "wheel", "uv", "altgraph", "pefile", "macholib", "pywin32-ctypes", "packaging-legacy"}
ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


# ── bundled distributions ────────────────────────────────────────────────────

def bundled(out: Path, *, sfs_version: str, platform: str) -> dict:
    """The distributions of the running environment, minus build tools: what
    the app built from it provides. Written at app build time."""
    from importlib import metadata
    dists = {}
    for d in metadata.distributions():
        name = _norm(d.metadata["Name"] or "")
        if name and name not in NOT_BUNDLED:
            dists[name] = d.version
    doc = {"sfs_version": sfs_version, "platform": platform, "python_abi": current_abi(),
           "distributions": dict(sorted(dists.items()))}
    Path(out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


# ── build ────────────────────────────────────────────────────────────────────

def _plugin_meta(src: Path) -> dict:
    import tomllib
    proj = tomllib.loads((src / "pyproject.toml").read_text())["project"]
    eps = proj.get("entry-points", {}).get("sfs.plugins", {})
    if len(eps) != 1:
        raise PackError(f"{src.name}: a pack needs exactly one sfs.plugins entry point, found {len(eps)}")
    (pid, register), = eps.items()
    return {"id": pid, "register": register, "name": proj["name"], "version": proj["version"],
            "description": proj.get("description", "")}


def _remove_dist(site: Path, dist_info: Path) -> None:
    record = dist_info / "RECORD"
    if record.exists():
        for line in record.read_text().splitlines():
            rel = line.split(",", 1)[0]
            p = (site / rel).resolve()
            if site.resolve() in p.parents and p.is_file():
                p.unlink()
    shutil.rmtree(dist_info, ignore_errors=True)
    for d in sorted((x for x in site.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()


def check_no_bundled(site: Path, provided: dict[str, str]) -> None:
    """The rule: a pack never contains a distribution the app provides."""
    from app.packs.store import site_distributions
    clash = sorted(set(site_distributions(site)) & set(provided))
    if clash:
        raise PackError(f"the pack contains {', '.join(clash)}, which the app provides; refusing it")


def build(src: Path, out: Path, *, bundled_file: Path, platform: str, sfs_version: str, sfs_max: str | None = None,
          edition: str = "community", licence_feature: str | None = None, key: Path | None = None,
          kid: str | None = None, python_abi: str | None = None, pip: list[str] | None = None,
          build_isolation: bool = True, include: list[Path] = ()) -> Path:
    src, out = Path(src).resolve(), Path(out)
    meta = _plugin_meta(src)
    provided = json.loads(Path(bundled_file).read_text())["distributions"]
    provided.pop(_norm(meta["name"]), None)          # a newer pack of a bundled plugin carries itself
    abi = python_abi or current_abi()
    pip = pip or [sys.executable, "-m", "pip"]
    work = Path(tempfile.mkdtemp(prefix="pack-"))
    try:
        wheels, site = work / "wheels", work / "pack" / "site"
        site.mkdir(parents=True)
        # The plugin, plus local packages it depends on that aren't on PyPI (e.g.
        # sfs-enterprise): given as files, so pip never looks those names up online.
        for s in [src, *map(Path, include)]:
            subprocess.run(pip + ["wheel", "--no-deps", "-q", *([] if build_isolation else ["--no-build-isolation"]),
                                  "-w", str(wheels), str(Path(s).resolve())], check=True)
        cons = work / "constraints.txt"
        cons.write_text("".join(f"{n}=={v}\n" for n, v in provided.items()))
        cross = platform.split("-", 1)[1] != _host_os_arch() or abi != current_abi()
        flags = ["-q", "--no-compile", "--target", str(site), *([] if build_isolation else ["--no-build-isolation"])]
        if cross:                                            # another platform or Python: wheels only
            flags += ["--only-binary", ":all:", "--python-version", abi[2] + "." + abi[3:], "--implementation", "cp",
                      *sum((["--platform", t] for t in _wheel_tags(platform)), [])]
        # 1. the plugin and the included packages themselves
        local = sorted(wheels.glob("*.whl"))
        subprocess.run(pip + ["install", "--no-deps", *flags, *map(str, local)], check=True)
        # 2. their requirements: the app's own copies are checked, not downloaded
        #    (some, like sfs-plugin-tm1, aren't on PyPI); the rest are resolved,
        #    pinned to the app's versions wherever they overlap
        ours = {_norm(w.name.split("-")[0]) for w in local}
        rest = _requirements(local, provided, ours, platform, abi)
        if rest:
            subprocess.run(pip + ["install", "-c", str(cons), *flags, *rest], check=True)
        shutil.rmtree(site / "bin", ignore_errors=True)
        for di in list(site.glob("*.dist-info")):
            name = _norm(di.name[:-len(".dist-info")].rpartition("-")[0])
            if name in provided:
                _remove_dist(site, di)                       # resolved against the app's own copy
        check_no_bundled(site, provided)
        from app.packs.store import site_distributions
        deps = [{"name": n, "version": v} for n, v in sorted(site_distributions(site).items()) if n != _norm(meta["name"])]
        manifest = check_manifest({
            "id": meta["id"], "name": meta["name"], "version": meta["version"], "edition": edition,
            "description": meta["description"], "sfs_min": sfs_version, "sfs_max": sfs_max or sfs_version,
            "platform": platform, "python_abi": abi, "register": meta["register"], "dependencies": deps,
            "sha256": tree_sha256(site), **({"licence_feature": licence_feature} if licence_feature else {})})
        raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        (work / "pack" / "manifest.json").write_bytes(raw)
        if key:
            sig = {"kid": kid, "signature": sign_bytes(raw, Path(key).expanduser().read_bytes())}
            (work / "pack" / "signature.json").write_text(json.dumps(sig) + "\n")
        dest = out / meta["id"] / meta["version"] / f"{platform}.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        _zip(work / "pack", dest)
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _requirements(wheels: list[Path], provided: dict[str, str], ours: set[str], platform: str, abi: str) -> list[str]:
    """Requirements of the given wheels that the app doesn't provide. One the
    app provides must accept the app's version, or the pack can't be built."""
    from email.parser import Parser
    from packaging.requirements import Requirement
    from packaging.version import Version
    os_name = platform.split("-")[1]
    env = {"python_version": f"{abi[2]}.{abi[3:]}", "python_full_version": f"{abi[2]}.{abi[3:]}.0",
           "sys_platform": {"windows": "win32", "macos": "darwin"}.get(os_name, "linux"),
           "platform_system": {"windows": "Windows", "macos": "Darwin"}.get(os_name, "Linux"),
           "os_name": "nt" if os_name == "windows" else "posix", "implementation_name": "cpython",
           "platform_python_implementation": "CPython", "extra": ""}
    rest = []
    for w in wheels:
        with zipfile.ZipFile(w) as zf:
            meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
            msg = Parser().parsestr(zf.read(meta).decode())
        for line in msg.get_all("Requires-Dist") or []:
            r = Requirement(line)
            if r.marker and not r.marker.evaluate(env):
                continue
            name = _norm(r.name)
            if name in ours:
                continue
            if name in provided:
                if r.specifier and not r.specifier.contains(Version(provided[name]), prereleases=True):
                    raise PackError(f"{w.name} needs {r}, but the app has {name} {provided[name]}")
                continue
            rest.append(str(r).split(";")[0].strip())
    return sorted(set(rest))


def _host_os_arch() -> str:
    from app.packs.fmt import current_platform
    return current_platform().split("-", 1)[1]


def _wheel_tags(platform: str) -> list[str]:
    os_arch = platform.split("-", 1)[1]
    return {"windows-x64": ["win_amd64"], "linux-x64": ["manylinux2014_x86_64", "manylinux_2_17_x86_64"],
            "linux-arm64": ["manylinux2014_aarch64", "manylinux_2_17_aarch64"],
            "macos-arm64": ["macosx_11_0_arm64"]}[os_arch]


def _zip(folder: Path, dest: Path) -> None:
    """Deterministic: sorted entries, fixed timestamps, so a rebuild hashes the same."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(x for x in folder.rglob("*") if x.is_file()):
            info = zipfile.ZipInfo(p.relative_to(folder).as_posix(), ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, p.read_bytes())


def sign(zip_path: Path, key: Path, kid: str) -> None:
    """Sign a built pack in place (the signing job holds the key; builders don't)."""
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        entries = {n: zf.read(n) for n in zf.namelist() if n != "signature.json"}
    if "manifest.json" not in entries:
        raise PackError(f"{zip_path} has no manifest.json")
    check_manifest(json.loads(entries["manifest.json"]))
    sig = {"kid": kid, "signature": sign_bytes(entries["manifest.json"], Path(key).expanduser().read_bytes())}
    entries["signature.json"] = (json.dumps(sig) + "\n").encode()
    tmp = zip_path.with_suffix(".signing")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in sorted(entries):
            info = zipfile.ZipInfo(n, ZIP_TIME)
            info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            zf.writestr(info, entries[n])
    os.replace(tmp, zip_path)


# ── catalog ──────────────────────────────────────────────────────────────────

def catalog(packs: list[Path], *, previous: dict | None, key: Path, kid: str, generated_at: str | None = None,
            withdraw: list[str] = ()) -> dict:
    """Merge built packs into the catalog and sign it. `withdraw` removes
    versions ("id==version"), e.g. after a security fix."""
    from app.packs.fmt import file_sha256
    cat = previous or {"format": "sfs-catalog", "plugins": []}
    by_id = {p["id"]: p for p in cat["plugins"]}
    for z in packs:
        with zipfile.ZipFile(z) as zf:
            m = json.loads(zf.read("manifest.json"))
            if "signature.json" not in zf.namelist():
                raise PackError(f"{z} is not signed")
        p = by_id.setdefault(m["id"], {"id": m["id"], "versions": []})
        p.update(name=m["name"], description=m.get("description", ""), edition=m["edition"])
        if m.get("licence_feature"):
            p["licence_feature"] = m["licence_feature"]
        v = next((x for x in p["versions"] if x["version"] == m["version"]), None)
        if v is None:
            v = {"version": m["version"], "sfs_min": m["sfs_min"], "sfs_max": m["sfs_max"], "packs": []}
            p["versions"].append(v)
        v["sfs_min"], v["sfs_max"] = m["sfs_min"], m["sfs_max"]
        v["packs"] = [x for x in v["packs"] if x["platform"] != m["platform"]] + [{
            "platform": m["platform"], "python_abi": m["python_abi"], "sha256": file_sha256(z),
            "size": Path(z).stat().st_size, "path": f"packs/{m['id']}/{m['version']}/{m['platform']}.zip"}]
    for w in withdraw:
        pid, _, ver = w.partition("==")
        if pid in by_id:
            by_id[pid]["versions"] = [v for v in by_id[pid]["versions"] if v["version"] != ver]
    cat["plugins"] = sorted(by_id.values(), key=lambda p: p["id"])
    cat["generated_at"] = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = json.dumps(cat, indent=2, sort_keys=True).encode()
    return {"kid": kid, "payload": base64.b64encode(payload).decode(),
            "signature": sign_bytes(payload, Path(key).expanduser().read_bytes())}


def _pyproject_version() -> str:
    import tomllib
    return tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())["project"]["version"]


def keygen(out: Path) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    out = Path(out).expanduser()
    if out.exists():
        raise SystemExit(f"{out} exists: refusing to overwrite a signing key")
    out.parent.mkdir(parents=True, exist_ok=True)
    k = Ed25519PrivateKey.generate()
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="app.packs.tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bundled"); b.add_argument("--out", type=Path, required=True)
    b.add_argument("--sfs", help="default: the version in pyproject.toml"); b.add_argument("--platform", help="default: this one")
    p = sub.add_parser("build"); p.add_argument("src", type=Path); p.add_argument("--out", type=Path, required=True)
    p.add_argument("--bundled", type=Path, required=True); p.add_argument("--platform", required=True)
    p.add_argument("--sfs", required=True); p.add_argument("--sfs-max"); p.add_argument("--edition", default="community")
    p.add_argument("--licence-feature"); p.add_argument("--key", type=Path); p.add_argument("--kid")
    p.add_argument("--include", type=Path, action="append", default=[],
                   help="a local package the plugin depends on, built and shipped in the pack")
    c = sub.add_parser("catalog"); c.add_argument("packs", nargs="*", type=Path)
    c.add_argument("--previous", type=Path); c.add_argument("--key", type=Path, required=True)
    c.add_argument("--kid", required=True); c.add_argument("--out", type=Path, required=True)
    c.add_argument("--withdraw", action="append", default=[])
    k = sub.add_parser("keygen"); k.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("sign"); g.add_argument("packs", nargs="+", type=Path)
    g.add_argument("--key", type=Path, required=True); g.add_argument("--kid", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "bundled":
        from app.packs.fmt import current_platform
        sfs = a.sfs or _pyproject_version()
        d = bundled(a.out, sfs_version=sfs, platform=a.platform or current_platform())
        print(f"{a.out}: {len(d['distributions'])} distributions")
    elif a.cmd == "build":
        print(build(a.src, a.out, bundled_file=a.bundled, platform=a.platform, sfs_version=a.sfs, sfs_max=a.sfs_max,
                    edition=a.edition, licence_feature=a.licence_feature, key=a.key, kid=a.kid, include=a.include))
    elif a.cmd == "catalog":
        prev = None
        if a.previous and a.previous.exists():
            from app.packs.fmt import signed_catalog
            prev = signed_catalog(json.loads(a.previous.read_text()))
        a.out.write_text(json.dumps(catalog(a.packs, previous=prev, key=a.key, kid=a.kid, withdraw=a.withdraw)) + "\n")
        print(a.out)
    elif a.cmd == "sign":
        for z in a.packs:
            sign(z, a.key, a.kid)
            print(f"signed {z}")
    else:
        print(keygen(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
