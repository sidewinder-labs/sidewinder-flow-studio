# PyInstaller spec for the SFS desktop backend sidecar (one-folder build).
#
#   cd desktop/sidecar && pyinstaller --noconfirm sfs-backend.spec
#
# Expects the backend, the plugins in SFS_DESKTOP_PLUGINS and PyInstaller to be
# installed in the running Python, and the frontend built into frontend/dist.
# Output: dist/sfs-backend/ (the executable plus its _internal/ folder).
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parents[1]          # repository root
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"
if not (FRONTEND_DIST / "index.html").exists():
    raise SystemExit("Build the frontend first: cd frontend && npm run build")

# Plugins bundled in-process, by short name (sfs-plugin-<name>): mirrors SFS_PLUGINS.
PLUGINS = [p for p in os.environ.get("SFS_DESKTOP_PLUGINS", "curvefit").split(",") if p]

datas = [(str(FRONTEND_DIST), "static")]
binaries = []
hiddenimports = []

def add_all(pkg):
    d, b, h = collect_all(pkg)
    datas.extend(d); binaries.extend(b); hiddenimports.extend(h)

# SFS itself: routers, engines and services are partly imported lazily.
hiddenimports += collect_submodules("app")
datas += collect_data_files("app.examples")             # sample data read as files

# Server stack chosen at run time by uvicorn / SQLAlchemy.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["aiosqlite", "sqlalchemy.dialects.sqlite.aiosqlite",
                  "sqlalchemy.dialects.postgresql.psycopg", "psycopg", "psycopg_binary"]

# Explore compiles charts with a WASM module driven through wasmtime.
add_all("gw_dsl_parser")
add_all("wasmtime")
# wasmtime loads its native library by path (wasmtime/<platform>/_libwasmtime.*)
# through ctypes, which collect_all misses: copy it to the same relative place.
import wasmtime as _wasmtime
_wt = Path(_wasmtime.__file__).parent
for lib in _wt.rglob("_libwasmtime.*"):
    binaries.append((str(lib), str(Path("wasmtime") / lib.parent.relative_to(_wt))))

# fast_read: ConnectorX, a Rust extension imported lazily.
add_all("connectorx")

# Libraries node code imports at run time, which analysis can't see.
for pkg in ("pandas", "numpy", "pyarrow", "duckdb", "polars", "scipy",
            "matplotlib", "seaborn", "plotly"):
    hiddenimports += collect_submodules(pkg, filter=lambda m: ".tests" not in m and "test_" not in m)
datas += collect_data_files("matplotlib")
datas += collect_data_files("plotly")

# The pushdown extra: SQLGlot powers Explore's live database queries and the SQL
# engine; Ibis is the Ibis engine. Ibis finds its backends through entry points.
for pkg in ("sqlglot", "ibis"):
    hiddenimports += collect_submodules(pkg, filter=lambda m: ".tests" not in m)
datas += collect_data_files("ibis")
datas += copy_metadata("ibis-framework") + copy_metadata("sqlglot")

# Plugins: their code, data files (templates and sample data are read as files)
# and the dist-info that holds the `sfs.plugins` entry point SFS discovers.
for short in PLUGINS:
    dist = f"sfs-plugin-{short}"
    module = f"sfs_plugin_{short}"
    hiddenimports += collect_submodules(module)
    datas += collect_data_files(module, include_py_files=True)
    datas += copy_metadata(dist)
for pkg in ("lmfit", "uncertainties", "sympy"):
    hiddenimports += collect_submodules(pkg, filter=lambda m: ".tests" not in m)

a = Analysis(
    ["entry.py"],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "IPython", "jupyter", "notebook", "streamlit"],
    noarchive=False,
)
# Test suites that packages ship as data are dead weight, and some paths in
# them are too long for Windows: ibis's snapshot files run past 260 characters,
# which makensis can't open.
a.datas = [entry for entry in a.datas if "tests" not in Path(entry[0]).parts]
pyz = PYZ(a.pure)
# macOS release builds: SFS_CODESIGN_IDENTITY (a Developer ID Application
# identity in the keychain) signs the executable and every collected binary
# with the hardened runtime, as notarisation requires. Unset: ad-hoc signing.
codesign_identity = os.environ.get("SFS_CODESIGN_IDENTITY") or None
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="sfs-backend",
          console=True, strip=False, upx=False,
          codesign_identity=codesign_identity,
          entitlements_file=str(Path(SPECPATH) / "entitlements.plist") if codesign_identity else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="sfs-backend")
