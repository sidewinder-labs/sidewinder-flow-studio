"""Function library: copies, not links. Extraction, import proposals, insert,
provenance states, updates, usage across projects, deprecate/delete, export."""
import asyncio

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import function_library as lib_api
from app.api import projects as projects_api
from app.core.config import settings
from app.engine import executor
from app.services import function_library as lib

FUNCS = '''import_me = 1


@staticmethod
def clean_phone(s):
    """Digits only."""
    # strip everything else
    return re.sub(r"\\D", "", str(s))


class Risk:
    LIMIT = 90


def classify(row):
    return "late" if row["dpd"] > Risk.LIMIT else "ok"
'''

IMPORTS = "import pandas as pd\nimport re, json\nfrom datetime import date, timedelta\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path / "frames"))
    app = FastAPI()
    app.include_router(lib_api.router, prefix="/api/function-library")
    app.include_router(projects_api.router, prefix="/api/projects")
    return TestClient(app)


# ── Reading code ─────────────────────────────────────────────────────────────

def test_top_level_finds_exact_source_with_decorators_and_docstrings():
    blocks = {b["name"]: b for b in lib.top_level(FUNCS)}
    assert list(blocks) == ["clean_phone", "Risk", "classify"]
    assert blocks["clean_phone"]["source"].startswith("@staticmethod\ndef clean_phone(s):")
    assert '"""Digits only."""' in blocks["clean_phone"]["source"]
    assert "# strip everything else" in blocks["clean_phone"]["source"]
    assert blocks["Risk"]["kind"] == "class" and blocks["Risk"]["source"] == "class Risk:\n    LIMIT = 90\n"


def test_save_proposes_only_the_imports_used():
    src = next(b for b in lib.top_level(FUNCS) if b["name"] == "clean_phone")["source"]
    assert lib.propose_imports(src, IMPORTS) == "import re\n"          # not json, from `import re, json`
    code = "def days_ago(n):\n    return date.today() - timedelta(days=n)\n"
    assert lib.propose_imports(code, IMPORTS) == "from datetime import date, timedelta\n"


# ── Insert and provenance ────────────────────────────────────────────────────

def test_insert_appends_code_and_adds_only_missing_imports(client):
    e = lib.create(name="days_ago", code="def days_ago(n):\n    return date.today() - timedelta(days=n)\n",
                   imports="from datetime import date, timedelta\nimport pandas as pd\n")
    out = lib.insert(functions=FUNCS, imports="import pandas as pd\n", provenance={}, entry_id=e["id"])
    assert out["functions"].rstrip().endswith("return date.today() - timedelta(days=n)")
    assert out["added_imports"] == ["from datetime import date, timedelta"]   # pandas already there
    assert out["imports"].count("import pandas as pd") == 1
    rec = out["provenance"]["days_ago"]
    assert rec["library_id"] == e["id"] and rec["version"] == 1


def test_name_collision_asks_then_replaces_or_renames(client):
    e = lib.create(name="classify", code="def classify(row):\n    return 'x'\n")
    with pytest.raises(lib.LibraryError, match="conflict:classify"):
        lib.insert(functions=FUNCS, imports="", provenance={}, entry_id=e["id"])
    replaced = lib.insert(functions=FUNCS, imports="", provenance={}, entry_id=e["id"], on_conflict="replace")
    assert replaced["functions"].count("def classify") == 1 and "return 'x'" in replaced["functions"]
    assert "Risk.LIMIT" not in replaced["functions"]
    renamed = lib.insert(functions=FUNCS, imports="", provenance={}, entry_id=e["id"],
                         on_conflict="rename", new_name="classify_v2")
    assert "def classify_v2(row)" in renamed["functions"] and "def classify(row)" in renamed["functions"]
    assert "classify_v2" not in renamed["provenance"]                  # inserted under a new name: the flow's own


def test_each_provenance_state_is_detected(client):
    e = lib.create(name="double", code="def double(x):\n    return x * 2\n")
    ins = lib.insert(functions="", imports="", provenance={}, entry_id=e["id"])
    f, prov = ins["functions"], ins["provenance"]
    assert lib.copy_states(f, prov)["double"]["state"] == "current"

    lib.update(e["id"], code="def double(x):\n    return 2 * x\n")
    assert lib.copy_states(f, prov)["double"]["state"] == "update"

    edited = f.replace("x * 2", "x + x")
    assert lib.copy_states(edited, prov)["double"]["state"] == "modified"

    lib.deprecate(e["id"], "use triple")
    st = lib.copy_states(f, prov)["double"]
    assert st["deprecated"] and st["deprecation_note"] == "use triple" and st["state"] == "update"

    lib.delete(e["id"])
    assert lib.copy_states(f, prov)["double"]["state"] == "deleted"


def test_update_replaces_the_copy_and_records_the_new_version(client):
    e = lib.create(name="double", code="def double(x):\n    return x * 2\n")
    ins = lib.insert(functions=FUNCS, imports="", provenance={}, entry_id=e["id"])
    lib.update(e["id"], code="def double(x):\n    return 2 * x\n", imports="import math\n")
    up = lib.update_copy(functions=ins["functions"], imports="", provenance=ins["provenance"], name="double")
    assert "return 2 * x" in up["functions"] and "x * 2" not in up["functions"]
    assert "def clean_phone" in up["functions"]                         # the rest untouched
    assert up["provenance"]["double"]["version"] == 2 and up["added_imports"] == ["import math"]
    assert lib.copy_states(up["functions"], up["provenance"])["double"]["state"] == "current"


def test_renaming_a_function_drops_its_provenance():
    prov = {"double": {"library_id": "x", "version": 1, "hash": "h"}}
    assert lib.prune("def twice(x):\n    return x * 2\n", prov) == {}
    assert lib.prune("def double(x):\n    return x * 2\n", prov) == prov


# ── Editing, usage, deprecate, delete ────────────────────────────────────────

def test_editing_increments_version_and_validates(client):
    e = lib.create(name="double", code="def double(x):\n    return x * 2\n", tags=["math"])
    assert lib.update(e["id"], description="Twice")["version"] == 2
    with pytest.raises(lib.LibraryError, match="doesn't parse"):
        lib.update(e["id"], code="def double(x:\n")
    with pytest.raises(lib.LibraryError, match="called 'double'"):
        lib.update(e["id"], code="def other(x):\n    return x\n")
    with pytest.raises(lib.LibraryError, match="only def and class"):
        lib.create(name="bad", code="import os\ndef bad():\n    pass\n")
    with pytest.raises(lib.LibraryError, match="already has"):
        lib.create(name="double", code="def double(x):\n    return x\n")


def _project_with(client, name, functions, provenance):
    pid = client.post("/api/projects/", json={"name": name}).json()["id"]
    flow = client.get(f"/api/projects/{pid}").json()["flows"][0]
    flow.update(shared_functions=functions, function_provenance=provenance)
    assert client.put(f"/api/projects/{pid}", json={"flows": [flow]}).status_code == 200
    return pid


def test_usage_across_projects_and_unused_filter(client):
    used = lib.create(name="double", code="def double(x):\n    return x * 2\n")
    unused = lib.create(name="halve", code="def halve(x):\n    return x / 2\n")
    a = lib.insert(functions="", imports="", provenance={}, entry_id=used["id"])
    _project_with(client, "A", a["functions"], a["provenance"])
    b = lib.insert(functions="", imports="", provenance={}, entry_id=used["id"])
    _project_with(client, "B", b["functions"].replace("x * 2", "x + x"), b["provenance"])

    entries = {e["name"]: e for e in client.get("/api/function-library/").json()}
    u = entries["double"]["usage"]
    assert u["count"] == 2 and u["last_inserted"]
    assert sorted(c["state"] for c in u["copies"]) == ["current", "modified"]
    assert entries["halve"]["usage"]["count"] == 0                     # the Unused filter's candidates
    assert {c["project"] for c in u["copies"]} == {"A", "B"}


def test_save_prunes_provenance_of_renamed_functions(client):
    e = lib.create(name="double", code="def double(x):\n    return x * 2\n")
    ins = lib.insert(functions="", imports="", provenance={}, entry_id=e["id"])
    pid = _project_with(client, "P", ins["functions"].replace("def double", "def twice"), ins["provenance"])
    assert client.get(f"/api/projects/{pid}").json()["flows"][0]["function_provenance"] == {}


def test_deprecated_entries_are_hidden_from_insert_list(client):
    e = lib.create(name="old", code="def old():\n    return 1\n")
    lib.deprecate(e["id"], "use new")
    assert [x["name"] for x in client.get("/api/function-library/?include_deprecated=false").json()] == []
    assert [x["name"] for x in client.get("/api/function-library/").json()] == ["old"]


# ── A flow never depends on the library ──────────────────────────────────────

def test_flow_with_copies_runs_after_entry_deleted_and_with_empty_library(client, tmp_path, monkeypatch):
    e = lib.create(name="double", code="def double(x):\n    return x * 2\n")
    ins = lib.insert(functions="", imports="import pandas as pd\n", provenance={}, entry_id=e["id"])
    lib.delete(e["id"])
    (tmp_path / "function_library.json").unlink(missing_ok=True)       # an instance with no library at all

    async def quiet(*a, **k): pass
    monkeypatch.setattr(executor.ws_manager, "broadcast", quiet)
    flow = {"id": "f", "name": "f", "imports": ins["imports"], "shared_functions": ins["functions"],
            "function_provenance": ins["provenance"], "edges": [
                {"id": "e", "source": "t", "target": "n", "sourceHandle": "out", "targetHandle": "in"}],
            "nodes": [{"id": "t", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"label": "Start"}},
                      {"id": "n", "type": "processor", "position": {"x": 0, "y": 0},
                       "data": {"label": "n", "engine": "pandas", "code": "df = pd.DataFrame({'v': [double(21)]})"}}]}
    results = asyncio.run(executor.execute_flow("p", flow, {}, {}))
    assert all(r["status"] == "ok" for r in results), results
    from app.core.frame_cache import load_frame
    assert load_frame("p", "n")["v"].tolist() == [42]


def test_export_and_import_with_custom_processors(client, tmp_path, monkeypatch):
    from app.services import custom_processor_service as cps
    lib.create(name="double", code="def double(x):\n    return x * 2\n", tags=["math"])
    cps.create_custom({"name": "My step", "code": "df = df"})
    exported = client.get("/api/function-library/export").json()
    assert exported["format"] == "sfs-library" and len(exported["functions"]) == 1
    assert len(exported["custom_processors"]) == 1

    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "other" / "projects"))   # another instance
    r = client.post("/api/function-library/import", json=exported).json()
    assert r == {"functions_added": 1, "functions_skipped": 0, "custom_processors_added": 1}
    assert client.post("/api/function-library/import", json=exported).json()["functions_skipped"] == 1
    assert client.post("/api/function-library/import", json={"x": 1}).status_code == 400


def test_api_conflict_is_a_409_naming_the_function(client):
    e = client.post("/api/function-library/", json={"name": "classify", "code": "def classify(r):\n    return 1\n"}).json()
    r = client.post("/api/function-library/insert", json={"functions": FUNCS, "imports": "", "entry_id": e["id"]})
    assert r.status_code == 409 and r.json()["detail"] == {"conflict": "classify"}
    fs = client.post("/api/function-library/functions", json={"functions": FUNCS}).json()["functions"]
    assert [f["name"] for f in fs] == ["clean_phone", "Risk", "classify"]
