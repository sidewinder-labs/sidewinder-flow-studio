"""In-app help: every helper is described, the generated docs are current, and
every example runs in its stated engine."""
import inspect
import shutil
from importlib.resources import files

import pandas as pd
import pyarrow as pa
import pytest
import sqlalchemy as sa

from app.engine import executor, registry
from app.help import examples as ex
from app.help import reference as ref

ROOT = ref._repo()


# ── The reference ────────────────────────────────────────────────────────────

def test_every_injected_helper_is_described():
    """Fails when someone adds a helper to node code without describing it."""
    injected = set(executor.build_exec_context({}, {})) - ref.NOT_HELPERS
    described = {name for name, *_ in ref.SFS_HELPERS}
    assert injected - described == set(), (
        f"Describe these in app/help/reference.py SFS_HELPERS (or NOT_HELPERS): {injected - described}")
    assert described - injected == set(), f"Described but not injected: {described - injected}"


def test_every_helper_documents_its_arguments_and_has_an_example():
    ctx = executor.build_exec_context({}, {})
    for h in ref.helper_reference():
        if not h["sfs_specific"]:
            assert h["summary"] and h["example"] and h["link"], h["name"]
            continue
        assert h["summary"] and h["example"], f"{h['name']}: needs a summary and an Example: section"
        if h["name"] in ctx and callable(ctx[h["name"]]):
            params = inspect.signature(ctx[h["name"]]).parameters.values()
            documented = {a["name"].lstrip("*") for a in h["args"]}
            named = {p.name for p in params if p.kind is not p.VAR_KEYWORD}
            # **kwargs is covered by naming it, or by documenting the options it takes
            missing = [p.name for p in params if p.name not in documented
                       and not (p.kind is p.VAR_KEYWORD and documented - named)]
            assert not missing, f"{h['name']}: document {missing} under Args:"


def test_agents_md_and_helpers_page_are_current():
    agents = (ROOT / "AGENTS.md").read_text()
    assert ref.agents_block_current(agents) == ref.agents_table(), \
        "AGENTS.md helper table is out of date: run python -m app.help.reference --write"
    assert (ROOT / "docs" / "HELPERS.md").read_text() == ref.helpers_page(), \
        "docs/HELPERS.md is out of date: run python -m app.help.reference --write"


def test_plugin_helpers_are_injected_and_listed(monkeypatch):
    monkeypatch.setattr(registry, "HELPERS", {})

    def shout(text: str) -> str:
        """Upper-case some text.

        Args:
            text: what to shout.

        Example:
            df["name"] = shout(df["name"])
        """
        return text.upper()

    api = registry.RegistryAPI("plugin:sfs-plugin-demo")
    api.register_helper("shout", shout, category="Text")
    assert executor.build_exec_context({}, {})["shout"]("hi") == "HI"
    h = next(h for h in ref.helper_reference() if h["name"] == "shout")
    assert h["signature"] == "shout(text)" and h["source"] == "plugin:sfs-plugin-demo"
    assert h["category"] == "Text" and h["args"] == [{"name": "text", "doc": "what to shout."}]
    with pytest.raises(ValueError):
        api.register_helper("_hidden", shout)


def test_capabilities_carry_the_help():
    from fastapi.testclient import TestClient
    from app.core.app_factory import create_app
    caps = TestClient(create_app()).get("/api/capabilities/").json()
    names = {h["name"] for h in caps["helpers"]}
    assert {"fast_read", "fast_write", "vars", "pd.read_csv"} <= names
    assert {s["id"] for s in caps["snippets"]} == {e["id"] for e in ex.EXAMPLES}


# ── Every example runs in its engine ─────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """data/ with the sample CSVs, and a SQLite connection called my_db."""
    data = tmp_path / "data"
    data.mkdir()
    sample = files("app.examples") / "sales.csv"
    shutil.copy(sample, data / "sales.csv")
    rows = pd.read_csv(data / "sales.csv")
    rows.iloc[:100].to_csv(data / "sales_2025.csv", index=False)
    rows.iloc[100:].to_csv(data / "sales_2026.csv", index=False)
    url = f"sqlite:///{tmp_path / 'my_db.sqlite'}"
    rows.to_sql("sales", sa.create_engine(url), index=False)
    monkeypatch.chdir(tmp_path)
    df = rows.assign(date=pd.to_datetime(rows["date"]))
    df2 = pd.DataFrame({"product": rows["product"].unique()}).assign(category="supplies")
    return {"url": url, "df": df, "df2": df2, "tmp": tmp_path}


def _frame_file(tmp, name, frame):
    path = tmp / f"{name}.arrow"
    table = pa.Table.from_pandas(frame, preserve_index=False)
    with pa.ipc.new_file(str(path), table.schema) as w:
        w.write_table(table)
    return str(path)


@pytest.mark.parametrize("example", ex.EXAMPLES, ids=[e["id"] for e in ex.EXAMPLES])
def test_example_runs_in_its_engine(example, workspace):
    ctx = executor.build_exec_context({"my_db": workspace["url"]}, dict(example.get("vars", {})),
                                      dialects={"my_db": "sqlite"})
    needs = example.get("needs", ())
    inputs = [workspace[n] for n in needs]
    engine, code = example["engine"], example["code"]

    if engine == "pandas":
        out = executor.exec_processor(code, inputs[0] if inputs else None, ctx, "", inputs[1:])
        assert isinstance(out, pd.DataFrame) and len(out) > 0
    elif engine == "polars":
        paths = [_frame_file(workspace["tmp"], n, workspace[n]) for n in needs]
        out = executor.exec_polars_processor(code, paths, ctx, "")
        out = pa.ipc.open_file(out.path).read_all() if not isinstance(out, pa.Table) else out
        assert out.num_rows > 0
    elif engine == "duckdb":
        paths = [_frame_file(workspace["tmp"], n, workspace[n]) for n in needs]
        out = executor.exec_duckdb_processor(code, paths)
        assert out.num_rows > 0
    elif engine == "sql":
        out = executor.exec_sql_processor(code, workspace["url"], "sqlite", None)
        assert len(out) > 0
    elif engine == "chart":
        out = executor.render_chart(inputs[0], code, ctx, inputs[1:])
        assert out["chart_type"] and out["payload"]
    else:
        pytest.fail(f"Unknown engine {engine}")

    if "fast_write" in code:
        written = pd.read_sql("SELECT COUNT(*) AS n FROM sales_copy", sa.create_engine(workspace["url"]))
        assert written["n"][0] == len(workspace["df"])
