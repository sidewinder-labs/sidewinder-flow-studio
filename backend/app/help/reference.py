"""The function reference: SFS helpers and the library functions they sit beside.

SFS helpers exist only inside SFS: they are the closures build_exec_context
injects into every node, so the reference is read from a context built with no
connections. Signatures and docstrings come from the functions themselves and
can't drift from the running code. Plugin helpers (api.register_helper) are
included automatically.

Library functions work anywhere Python runs. Each entry says so, so users learn
which skills transfer outside SFS.

The same data renders three ways: /api/capabilities (the help panel), the table
in AGENTS.md and docs/HELPERS.md. tests/test_help.py fails when the last two
are out of date; regenerate them with:

    python -m app.help.reference --write
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

# Built-in helpers: (name, category, engines). Docs come from the functions.
SFS_HELPERS = (
    ("fast_read", "Read data", ("pandas", "polars")),
    ("read_tables", "Read data", ("pandas", "polars")),
    ("read_files", "Read data", ("pandas", "polars")),
    ("read_delta", "Read data", ("pandas", "polars")),
    ("read_iceberg", "Read data", ("pandas", "polars")),
    ("list_files", "Files", ("pandas", "polars")),
    ("fast_write", "Write data", ("pandas", "polars")),
    ("get_engine", "Connections", ("pandas", "polars")),
    ("get_storage", "Connections", ("pandas", "polars")),
    ("vars", "Variables", ("pandas", "polars", "chart")),
)

VARS_DOC = """Your global Variables (Meta → Variables), as a dict of strings.

For settings that change between environments, such as a region or a date,
not for secrets: passwords belong in a connection.

Example:
    df = df[df["region"] == vars["region"]]
"""

# Names injected into node scope that aren't helpers to document: modules and
# internals. A test fails if a new injected name is in neither list.
NOT_HELPERS = {"__builtins__", "pd", "sa", "_fast_read_arrow"}

LIBRARY = (
    {"name": "pd.read_csv", "signature": "pd.read_csv(path, sep=',', ...)", "category": "Read data",
     "engines": ("pandas",), "library": "pandas",
     "summary": "Read a CSV file into a pandas DataFrame.",
     "link": "https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html",
     "example": 'df = pd.read_csv("data/sales.csv", parse_dates=["date"])'},
    {"name": "pd.read_parquet", "signature": "pd.read_parquet(path, columns=None, ...)", "category": "Read data",
     "engines": ("pandas",), "library": "pandas",
     "summary": "Read a Parquet file (or folder of files) into a pandas DataFrame.",
     "link": "https://pandas.pydata.org/docs/reference/api/pandas.read_parquet.html",
     "example": 'df = pd.read_parquet("data/sales.parquet")'},
    {"name": "pd.read_excel", "signature": "pd.read_excel(path, sheet_name=0, ...)", "category": "Read data",
     "engines": ("pandas",), "library": "pandas",
     "summary": "Read a sheet of an Excel workbook into a pandas DataFrame.",
     "link": "https://pandas.pydata.org/docs/reference/api/pandas.read_excel.html",
     "example": 'df = pd.read_excel("data/budget.xlsx", sheet_name="2026")'},
    {"name": "pd.read_sql", "signature": "pd.read_sql(sql, con, ...)", "category": "Read data",
     "engines": ("pandas",), "library": "pandas",
     "summary": "Run a query and load the result into pandas. Works anywhere, but slow for large "
                "results; in SFS, fast_read does the same much faster.",
     "link": "https://pandas.pydata.org/docs/reference/api/pandas.read_sql.html",
     "example": 'df = pd.read_sql("SELECT * FROM sales", get_engine("my_db"))'},
    {"name": "pl.scan_csv", "signature": "pl.scan_csv(path, ...)", "category": "Read data",
     "engines": ("polars",), "library": "Polars",
     "summary": "Scan a CSV file lazily: nothing is read until the plan runs, and only the "
                "columns and rows it needs.",
     "link": "https://docs.pola.rs/api/python/stable/reference/api/polars.scan_csv.html",
     "example": 'df = pl.scan_csv("data/sales.csv")'},
    {"name": "pl.scan_parquet", "signature": "pl.scan_parquet(path, ...)", "category": "Read data",
     "engines": ("polars",), "library": "Polars",
     "summary": "Scan Parquet files lazily, reading only what the plan needs.",
     "link": "https://docs.pola.rs/api/python/stable/reference/api/polars.scan_parquet.html",
     "example": 'df = pl.scan_parquet("data/*.parquet")'},
    {"name": "pl.read_database", "signature": "pl.read_database(query, connection, ...)", "category": "Read data",
     "engines": ("polars",), "library": "Polars",
     "summary": "Run a query through a SQLAlchemy engine into a Polars DataFrame. In SFS, "
                "fast_read is usually faster.",
     "link": "https://docs.pola.rs/api/python/stable/reference/api/polars.read_database.html",
     "example": 'df = pl.read_database("SELECT * FROM sales", get_engine("my_db"))'},
    {"name": "read_csv_auto", "signature": "read_csv_auto('path or glob', ...)", "category": "Read data",
     "engines": ("duckdb",), "library": "DuckDB",
     "summary": "DuckDB SQL: read CSV files, detecting the format. A glob reads many files; "
                "filename = true adds a column with each row's file.",
     "link": "https://duckdb.org/docs/data/csv/overview",
     "example": "SELECT * FROM read_csv_auto('data/sales_*.csv', filename = true)"},
    {"name": "read_parquet", "signature": "read_parquet('path or glob')", "category": "Read data",
     "engines": ("duckdb",), "library": "DuckDB",
     "summary": "DuckDB SQL: query Parquet files directly, reading only the columns used.",
     "link": "https://duckdb.org/docs/data/parquet/overview",
     "example": "SELECT region, SUM(quantity) AS units FROM read_parquet('data/*.parquet') GROUP BY region"},
    {"name": "df.to_csv / df.to_parquet", "signature": "df.to_parquet(path) · df.write_parquet(path)",
     "category": "Write data", "engines": ("pandas", "polars"), "library": "pandas / Polars",
     "summary": "Write a DataFrame to a file. pandas: to_csv, to_parquet, to_excel; Polars: "
                "write_csv, write_parquet (collect a LazyFrame first, or sink_parquet).",
     "link": "https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_parquet.html",
     "example": 'df.to_parquet("out/sales_clean.parquet")'},
)


def _canonical_context() -> dict:
    from app.engine.executor import build_exec_context
    return build_exec_context({}, {})


def _signature(name: str, func) -> str:
    """name(params) with defaults but no type annotations: shorter, and easier to
    read for people new to Python. Types are in the argument docs."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return f"{name}(...)"
    params = [p.replace(annotation=inspect.Parameter.empty) for p in sig.parameters.values()]
    return f"{name}{sig.replace(parameters=params, return_annotation=inspect.Signature.empty)}"


def _parse_doc(doc: str) -> dict:
    """Split a helper docstring into summary, body, args, returns and example."""
    doc = inspect.cleandoc(doc or "")
    sections = re.split(r"^(Args|Returns|Example):\s*$", doc, flags=re.M)
    intro, rest = sections[0].strip(), sections[1:]
    summary, _, body = intro.partition("\n\n")
    out = {"summary": " ".join(summary.split()), "description": " ".join(body.split()),
           "args": [], "returns": "", "example": ""}
    for head, text in zip(rest[::2], rest[1::2]):
        text = inspect.cleandoc(text)
        if head == "Args":
            for m in re.finditer(r"^(\**\w+):\s*(.*(?:\n(?!\**\w+:).*)*)", text, re.M):
                out["args"].append({"name": m.group(1), "doc": " ".join(m.group(2).split())})
        elif head == "Returns":
            out["returns"] = " ".join(text.split())
        else:
            out["example"] = text
    return out


def helper_reference() -> list[dict]:
    """Every helper and library function, for /api/capabilities and the docs."""
    from app.engine.registry import HELPERS

    ctx = _canonical_context()
    out = []
    for name, category, engines in SFS_HELPERS:
        if name == "vars":
            doc, sig = VARS_DOC, 'vars["name"]'
        else:
            func = ctx[name]
            doc, sig = inspect.getdoc(func) or "", _signature(name, func)
        out.append({"name": name, "signature": sig, "category": category, "engines": list(engines),
                    "sfs_specific": True, "source": "builtin", "link": None, **_parse_doc(doc)})
    for name, h in HELPERS.items():
        doc = _parse_doc(inspect.getdoc(h["func"]) or "")
        if not doc["example"] and h["examples"]:
            doc["example"] = h["examples"][0].get("code", "")
        out.append({"name": name, "signature": _signature(name, h["func"]), "category": h["category"],
                    "engines": ["pandas", "polars"], "sfs_specific": True, "source": h["source"],
                    "link": None, **doc})
    for lib in LIBRARY:
        out.append({"name": lib["name"], "signature": lib["signature"], "category": lib["category"],
                    "engines": list(lib["engines"]), "sfs_specific": False, "source": lib["library"],
                    "link": lib["link"], "summary": lib["summary"], "description": "",
                    "args": [], "returns": "", "example": lib["example"]})
    return out


def library_reference() -> list[dict]:
    """The user's function library, for the help panel only: these functions
    exist in a flow only once copied into its Functions, so they are not
    injected helpers (and stay out of AGENTS.md, HELPERS.md and the AI prompt)."""
    import ast
    from app.services import function_library as fl
    out = []
    for e in fl.list_entries():
        sig, doc = e["name"], ""
        try:
            node = next(n for n in ast.parse(e["code"]).body if getattr(n, "name", None) == e["name"])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig = f"{e['name']}({ast.unparse(node.args)})"
            doc = ast.get_docstring(node) or ""
        except (SyntaxError, StopIteration):
            pass
        parsed = _parse_doc(doc)
        out.append({"name": e["name"], "signature": sig, "category": "Your library",
                    "engines": ["pandas", "polars"], "sfs_specific": False, "source": "Your library",
                    "library": True, "link": None, "deprecated": e["deprecated"],
                    "summary": e["description"] or parsed["summary"], "description": parsed["description"],
                    "args": parsed["args"], "returns": parsed["returns"],
                    "example": e["code"], "tags": e["tags"], "version": e["version"]})
    return out


# ── Markdown renderings, kept in step by tests/test_help.py ──────────────────

AGENTS_START = "<!-- helpers:start (generated by python -m app.help.reference --write) -->"
AGENTS_END = "<!-- helpers:end -->"


def agents_table() -> str:
    rows = ["| Name | Meaning |", "|---|---|", "| `pd`, `sa` | pandas, sqlalchemy |"]
    for h in helper_reference():
        if h["sfs_specific"] and h["source"] == "builtin":
            cell = lambda t: t.replace("|", "\\|")             # a bare | splits a table cell
            rows.append(f"| `{cell(h['signature'])}` | {cell(h['summary'])} |")
    return "\n".join(rows)


def helpers_page() -> str:
    """docs/HELPERS.md: the full reference, for the docs site and GitHub."""
    ref = helper_reference()
    lines = ["# Functions in node code", "",
             "<!-- Generated by `python -m app.help.reference --write` from the helpers' own",
             "     signatures and docstrings. Edit those, not this file. -->", "",
             "**SFS helpers** exist only inside Sidewinder Flow Studio and need no import. "
             "**Library functions** are standard pandas, Polars and DuckDB: they work anywhere "
             "Python runs, so what you learn here carries over.", "", "## SFS helpers", ""]
    for h in (h for h in ref if h["sfs_specific"] and h["source"] == "builtin"):
        lines += [f"### `{h['name']}`", "", "```python", h["signature"], "```", "", h["summary"], ""]
        if h["description"]:
            lines += [h["description"], ""]
        if h["args"]:
            lines += [f"- `{a['name']}`: {a['doc']}" for a in h["args"]] + [""]
        if h["returns"]:
            lines += [f"**Returns:** {h['returns']}", ""]
        if h["example"]:
            lines += ["```python", h["example"], "```", ""]
    lines += ["## Library functions", "", "| Function | Engine | What it does |", "|---|---|---|"]
    for h in (h for h in ref if not h["sfs_specific"]):
        lines.append(f"| [`{h['name']}`]({h['link']}) | {h['source']} | {h['summary']} |")
    return "\n".join(lines) + "\n"


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def agents_block_current(text: str) -> str | None:
    """The generated block currently in AGENTS.md, or None if the markers are missing."""
    i, j = text.find(AGENTS_START), text.find(AGENTS_END)
    return None if i < 0 or j < 0 else text[i + len(AGENTS_START):j].strip("\n")


def write() -> list[str]:
    root, changed = _repo(), []
    agents = root / "AGENTS.md"
    text = agents.read_text()
    i, j = text.find(AGENTS_START), text.find(AGENTS_END)
    if i < 0 or j < 0:
        raise SystemExit(f"AGENTS.md has no {AGENTS_START} … {AGENTS_END} block")
    new = text[:i + len(AGENTS_START)] + "\n" + agents_table() + "\n" + text[j:]
    if new != text:
        agents.write_text(new); changed.append("AGENTS.md")
    page = root / "docs" / "HELPERS.md"
    if not page.exists() or page.read_text() != helpers_page():
        page.write_text(helpers_page()); changed.append("docs/HELPERS.md")
    return changed


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        print("updated:", ", ".join(write()) or "nothing (already current)")
    else:
        raise SystemExit(__doc__)
