"""Plugin packs: building (no bundled distributions), signing, install,
tamper detection, compatibility, safe startup, safe mode, rollback, catalog
replay protection, offline use, overriding a bundled plugin, and the API."""
import base64
import json
import sys
import zipfile
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.config import settings
from app.engine import registry
from app.packs import catalog, keys, store, tools
from app.packs.fmt import PackError, current_abi, current_platform

PLAT = current_platform()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "data" / "projects"))
    monkeypatch.delenv("SFS_SAFE_MODE", raising=False)
    monkeypatch.delenv("SFS_LICENSE", raising=False)
    k = Ed25519PrivateKey.generate()
    pem = tmp_path / "test.pem"
    pem.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    monkeypatch.setattr(keys, "PLUGIN_KEYS", {"t1": pub})
    bundled = tmp_path / "bundled.json"
    tools.bundled(bundled, sfs_version="1.39", platform=PLAT)
    store.LOADED.clear()
    yield {"tmp": tmp_path, "pem": pem, "bundled": bundled}
    for name in [n for n in registry.ENGINES if n.startswith("packtest_")]:
        registry.ENGINES.pop(name)
    for m in [m for m in sys.modules if m.startswith("sfs_packtest")]:
        sys.modules.pop(m)
    sys.meta_path[:] = [f for f in sys.meta_path if type(f).__name__ != "_PackFinder"]
    sys.path[:] = [p for p in sys.path if not p.startswith(str(tmp_path))]
    import importlib
    importlib.invalidate_caches()


def plugin_src(root: Path, *, pid="packtest", version="0.1.0", body=None, dep=True):
    """A tiny plugin: one engine; depends on pandas (the app has it) and on a
    small local library (it doesn't)."""
    src = root / f"src-{pid}-{version}"
    mod = src / f"sfs_{pid}"
    mod.mkdir(parents=True)
    deps = ['"pandas"']
    if dep:
        lib = root / "lib-dep"
        if not lib.exists():
            (lib / "sfs_packtest_dep").mkdir(parents=True)
            (lib / "sfs_packtest_dep" / "__init__.py").write_text("VALUE = 42\n")
            (lib / "pyproject.toml").write_text('[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n'
                                                '[project]\nname="sfs-packtest-dep"\nversion="1.0"\n')
        deps.append(f'"sfs-packtest-dep @ {lib.as_uri()}"')
    (src / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n'
        f'[project]\nname="sfs-{pid}"\nversion="{version}"\ndescription="Test pack {version}"\ndependencies=[{",".join(deps)}]\n'
        f'[project.entry-points."sfs.plugins"]\n{pid}="sfs_{pid}.plugin:register"\n'
        f'[tool.setuptools]\npackages=["sfs_{pid}"]\n')
    (mod / "__init__.py").write_text("")
    (mod / "plugin.py").write_text(body or (
        "from sfs_packtest_dep import VALUE\n"
        "def register(api):\n"
        f"    api.register_engine(api.EngineSpec(name='{pid}_engine', label='Pack test {version}', run=lambda ec: None))\n"))
    return src


def build(env, src, *, sign=True, sfs="1.39", sfs_max=None):
    return tools.build(src, env["tmp"] / "dist", bundled_file=env["bundled"], platform=PLAT, sfs_version=sfs,
                       sfs_max=sfs_max, key=env["pem"] if sign else None, kid="t1", build_isolation=False)


def api_factory(source):
    return registry.RegistryAPI(source=source)


# ── building ─────────────────────────────────────────────────────────────────

def test_build_leaves_out_what_the_app_provides(env):
    z = build(env, plugin_src(env["tmp"]))
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        m = json.loads(zf.read("manifest.json"))
    assert "signature.json" in names and any(n.startswith("site/sfs_packtest/") for n in names)
    assert any(n.startswith("site/sfs_packtest_dep/") for n in names)                # its own dependency
    assert not any(n.startswith("site/pandas") or n.startswith("site/numpy") for n in names)   # the app's
    assert [d["name"] for d in m["dependencies"]] == ["sfs-packtest-dep"]
    assert m["platform"] == PLAT and m["python_abi"] == current_abi() and m["register"] == "sfs_packtest.plugin:register"
    assert build(env, plugin_src(env["tmp"] / "again")).read_bytes() == z.read_bytes()   # deterministic


def test_signing_a_built_pack_later(env):
    z = build(env, plugin_src(env["tmp"]), sign=False)
    tools.sign(z, env["pem"], "t1")
    assert store.install_zip(z, "1.39")["id"] == "packtest"


def test_builder_refuses_a_plugin_needing_another_version_of_an_app_library(env):
    src = plugin_src(env["tmp"], dep=False)
    t = (src / "pyproject.toml").read_text().replace('"pandas"', '"pandas>=99"')
    (src / "pyproject.toml").write_text(t)
    with pytest.raises(PackError, match="needs pandas>=99, but the app has pandas"):
        build(env, src)


def test_builder_refuses_a_pack_holding_a_bundled_distribution(env):
    site = env["tmp"] / "site"
    (site / "numpy-2.0.0.dist-info").mkdir(parents=True)
    with pytest.raises(PackError, match="contains numpy, which the app provides"):
        tools.check_no_bundled(site, {"numpy": "2.0.0"})


# ── installing and loading ───────────────────────────────────────────────────

def test_install_and_load(env):
    m = store.install_zip(build(env, plugin_src(env["tmp"])), "1.39")
    assert (store.root() / "packtest" / "0.1.0" / "site").is_dir()
    assert store.load_all(api_factory, "1.39") == {"packtest"}
    assert registry.ENGINES["packtest_engine"].source == "pack:packtest"
    assert store.installed()[0] | {} and store.installed()[0]["loaded"] and m["version"] == "0.1.0"


def test_unsigned_and_foreign_packs_are_refused(env, monkeypatch):
    with pytest.raises(PackError, match="no readable manifest.json and signature.json"):
        store.install_zip(build(env, plugin_src(env["tmp"]), sign=False), "1.39")
    z = build(env, plugin_src(env["tmp"] / "b"))
    monkeypatch.setattr(keys, "PLUGIN_KEYS", {"t1": base64.b64encode(b"\1" * 32).decode()})
    with pytest.raises(PackError, match="signature is invalid"):
        store.install_zip(z, "1.39")


def test_tampered_pack_is_refused_before_install(env):
    z = build(env, plugin_src(env["tmp"]))
    bad = env["tmp"] / "bad.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(bad, "w") as dst:
        for i in src.infolist():
            data = src.read(i)
            if i.filename == "site/sfs_packtest/plugin.py":
                data += b"\nimport os  # injected\n"
            dst.writestr(i, data)
    with pytest.raises(PackError, match="files differ from what was signed"):
        store.install_zip(bad, "1.39")
    assert not (store.folder() / "packtest").exists()


def test_tampered_installed_files_are_caught_at_startup(env):
    store.install_zip(build(env, plugin_src(env["tmp"])), "1.39")
    f = store.root() / "packtest" / "0.1.0" / "site" / "sfs_packtest" / "plugin.py"
    f.write_text(f.read_text() + "\n# changed on disk\n")
    assert store.load_all(api_factory, "1.39") == set()
    st = store.installed()[0]
    assert not st["enabled"] and "changed on disk" in st["error"] and "packtest_engine" not in registry.ENGINES


def test_zip_slip_is_refused(env):
    z = env["tmp"] / "slip.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../evil.py", "x")
    with pytest.raises(PackError, match="outside the pack"):
        store.install_zip(z, "1.39")


def test_incompatible_version_is_refused_and_disabled_after_an_app_update(env):
    z = build(env, plugin_src(env["tmp"]), sfs="1.39", sfs_max="1.39")
    with pytest.raises(PackError, match="needs SFS 1.39 or newer"):
        store.install_zip(z, "1.38")
    store.install_zip(z, "1.39")
    assert store.load_all(api_factory, "1.39.2") == {"packtest"}          # a patch release still fits
    store.LOADED.clear()
    assert store.load_all(api_factory, "1.40") == set()
    st = store.installed()[0]
    assert not st["enabled"] and "after the update to SFS 1.40" in st["notice"]


def test_a_plugin_that_raises_leaves_the_app_running(env):
    src = plugin_src(env["tmp"], pid="packtest_bad", body="raise RuntimeError('broken on import')\n", dep=False)
    store.install_zip(build(env, src), "1.39")
    store.install_zip(build(env, plugin_src(env["tmp"])), "1.39")
    assert store.load_all(api_factory, "1.39") == {"packtest"}             # the good one still loads
    bad = next(p for p in store.installed() if p["id"] == "packtest_bad")
    assert not bad["enabled"] and "RuntimeError: broken on import" in bad["error"]


def test_a_crash_while_loading_disables_that_plugin_next_time(env):
    store.install_zip(build(env, plugin_src(env["tmp"])), "1.39")
    (store.root() / "loading.json").write_text(json.dumps({"id": "packtest"}))   # what a crash leaves behind
    assert store.load_all(api_factory, "1.39") == set()
    st = store.installed()[0]
    assert not st["enabled"] and "SFS stopped while loading" in st["error"]
    store.set_enabled("packtest", True)
    assert store.load_all(api_factory, "1.39") == {"packtest"}


def test_safe_mode_loads_nothing(env, monkeypatch):
    store.install_zip(build(env, plugin_src(env["tmp"])), "1.39")
    store.set_safe_mode(True)
    assert store.load_all(api_factory, "1.39") == set() and store.installed()[0]["enabled"]
    store.set_safe_mode(False)
    monkeypatch.setenv("SFS_SAFE_MODE", "1")
    assert store.load_all(api_factory, "1.39") == set()


def test_safe_mode_skips_entry_point_plugins_too(env, monkeypatch):
    monkeypatch.setenv("SFS_SAFE_MODE", "1")
    monkeypatch.setattr(registry, "_PLUGINS_LOADED", False)
    monkeypatch.setattr(registry, "_LOADED_PLUGINS", [])
    monkeypatch.setattr(registry, "EXAMPLES", [])
    out = registry.load_plugins()
    assert out and all(p["error"] == "safe mode" and not p["loaded"] for p in out)


def test_rollback_restores_the_previous_version(env):
    store.install_zip(build(env, plugin_src(env["tmp"], version="0.1.0")), "1.39")
    store.install_zip(build(env, plugin_src(env["tmp"], version="0.2.0")), "1.39")
    assert store.installed()[0]["version"] == "0.2.0" and store.installed()[0]["previous"] == "0.1.0"
    assert store.rollback("packtest") == "0.1.0"
    store.load_all(api_factory, "1.39")
    assert registry.ENGINES["packtest_engine"].label == "Pack test 0.1.0"
    store.install_zip(build(env, plugin_src(env["tmp"], version="0.3.0")), "1.39")
    assert sorted(d.name for d in (store.root() / "packtest").iterdir()) == ["0.1.0", "0.3.0"]   # only one kept back


def test_a_pack_of_a_bundled_plugin_carries_the_plugin_itself(env):
    import json as _j
    b = _j.loads(env["bundled"].read_text())
    b["distributions"]["sfs-packtest"] = "0.0.1"                         # the app bundles an older version
    env["bundled"].write_text(_j.dumps(b))
    z = build(env, plugin_src(env["tmp"], version="0.2.0"))
    with zipfile.ZipFile(z) as zf:
        assert any(n.startswith("site/sfs_packtest/") for n in zf.namelist())
    assert store.install_zip(z, "1.39", provided={"sfs-packtest": "0.0.1", "pandas": "3"})["version"] == "0.2.0"


def test_a_newer_pack_replaces_the_bundled_plugin_an_older_one_doesnt(env):
    store.install_zip(build(env, plugin_src(env["tmp"], version="0.2.0")), "1.39")
    assert store.load_all(api_factory, "1.39", {"packtest": "0.1.0"}) == {"packtest"}
    store.LOADED.clear()
    assert store.load_all(api_factory, "1.39", {"packtest": "0.5.0"}) == set()
    assert "bundled version (0.5.0)" in store.installed()[0]["notice"]


# ── catalog ──────────────────────────────────────────────────────────────────

def signed(env, packs, *, previous=None, when=None, withdraw=()):
    return tools.catalog(packs, previous=previous, key=env["pem"], kid="t1", generated_at=when, withdraw=withdraw)


def test_catalog_offers_only_what_fits_and_rejects_replays(env):
    old = build(env, plugin_src(env["tmp"], version="0.1.0"), sfs="1.30", sfs_max="1.30")
    new = build(env, plugin_src(env["tmp"], version="0.2.0"), sfs="1.39")
    doc = signed(env, [old, new], when="2026-11-07T10:00:00Z")
    cat = catalog.accept(doc)
    assert catalog.offers(cat, "1.39")[0]["versions"] == ["0.2.0"]           # 0.1.0 is for SFS 1.30 only
    assert catalog.offers(cat, "1.39", platform="desktop-macos-arm64")[0]["versions"] == []
    with pytest.raises(PackError, match="older"):
        catalog.accept(signed(env, [old], when="2026-11-06T10:00:00Z"))
    catalog.accept(signed(env, [new], when="2026-11-08T10:00:00Z"))
    bad = dict(doc, payload=base64.b64encode(base64.b64decode(doc["payload"]).replace(b"0.2.0", b"9.9.9")).decode())
    with pytest.raises(PackError, match="signature is invalid"):
        catalog.accept(bad)


def mock_server(env, zips, *, require_licence=False):
    files = {f"/packs/{json.loads(zipfile.ZipFile(z).read('manifest.json'))['id']}/"
             f"{json.loads(zipfile.ZipFile(z).read('manifest.json'))['version']}/{PLAT}.zip": z.read_bytes() for z in zips}
    seen = []

    def h(req):
        seen.append(req)
        if require_licence and not req.headers.get("Authorization", "").startswith("Licence SFS-LICENSE-1."):
            return httpx.Response(401, json={"error": "a valid Enterprise licence is required"})
        return httpx.Response(200, content=files[req.url.path]) if req.url.path in files else httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(h)), seen


def test_download_checks_the_checksum_and_installs(env, monkeypatch):
    z = build(env, plugin_src(env["tmp"]))
    catalog.accept(signed(env, [z]))
    client, _ = mock_server(env, [z])
    assert catalog.download_and_install("packtest", None, "1.39", http=client)["version"] == "0.1.0"
    other = build(env, plugin_src(env["tmp"] / "x", version="0.1.0", dep=False))
    client2, _ = mock_server(env, [other])
    with pytest.raises(PackError, match="doesn't match the catalog's checksum"):
        catalog.download_and_install("packtest", "0.1.0", "1.39", http=client2)


def test_enterprise_pack_needs_a_licence_key(env, monkeypatch):
    z = tools.build(plugin_src(env["tmp"]), env["tmp"] / "dist", bundled_file=env["bundled"], platform=PLAT,
                    sfs_version="1.39", edition="enterprise", licence_feature="tm1_modeling", key=env["pem"], kid="t1",
                    build_isolation=False)
    catalog.accept(signed(env, [z]))
    client, seen = mock_server(env, [z], require_licence=True)
    with pytest.raises(PackError, match="add your licence key first"):
        catalog.download_and_install("packtest", None, "1.39", http=client)
    assert seen == []                                                     # nothing sent without a key
    monkeypatch.setenv("SFS_LICENSE", "SFS-LICENSE-1.e30.c2ln")
    catalog.download_and_install("packtest", None, "1.39", http=client)
    assert seen[0].headers["Authorization"] == "Licence SFS-LICENSE-1.e30.c2ln"


def test_offline_the_cached_catalog_and_installed_plugins_still_work(env):
    z = build(env, plugin_src(env["tmp"]))
    catalog.accept(signed(env, [z]))
    store.install_zip(z, "1.39")
    dead = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("offline"))))
    with pytest.raises(PackError, match="couldn't be read"):
        catalog.fetch(http=dead)
    assert catalog.cached()["plugins"][0]["id"] == "packtest"
    assert store.load_all(api_factory, "1.39") == {"packtest"}


def test_withdrawn_versions_leave_the_catalog(env):
    z = build(env, plugin_src(env["tmp"]))
    doc = signed(env, [z], when="2026-11-07T10:00:00Z")
    from app.packs.fmt import signed_catalog
    doc2 = signed(env, [], previous=signed_catalog(doc), when="2026-11-08T10:00:00Z", withdraw=["packtest==0.1.0"])
    assert catalog.offers(catalog.accept(doc2), "1.39")[0]["versions"] == []


# ── API ──────────────────────────────────────────────────────────────────────

def test_plugins_api(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import plugins as plugins_api
    app = FastAPI(version="1.39")
    app.include_router(plugins_api.router, prefix="/api/plugins")
    c = TestClient(app)
    z = build(env, plugin_src(env["tmp"]))
    catalog.accept(signed(env, [z]))
    v = c.get("/api/plugins/").json()
    assert [a["id"] for a in v["available"]] == ["packtest"] and v["platform"] == PLAT and not v["safe_mode"]
    r = c.post("/api/plugins/upload", files={"file": ("p.zip", z.read_bytes(), "application/zip")}).json()
    assert r["installed"][0]["id"] == "packtest" and r["available"] == [] and r["restart_needed"]
    assert c.post("/api/plugins/packtest/disable").json()["installed"][0]["enabled"] is False
    assert c.post("/api/plugins/packtest/rollback").status_code == 400
    assert c.put("/api/plugins/safe-mode", json={"on": True}).json()["safe_mode"]
    assert c.put("/api/plugins/licence", json={"text": "hello"}).status_code == 400
    assert c.post("/api/plugins/packtest/remove").json()["installed"] == []
    assert c.post("/api/plugins/restart").status_code == 400                   # a source install can't restart itself
