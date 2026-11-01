"""Graphic Walker DSL → SQL.

The only module that touches `gw-dsl-parser`. We load its WASM build ourselves
instead of calling `gw_dsl_parser.get_sql_from_payload`, because the package:

  * instantiates every call into one long-lived wasmtime Store, so memory grows
    without bound in a server process (~220 MB per 1000 calls, measured);
  * passes the whole process environment (SECRET_KEY included) into the WASM
    sandbox via `inherit_env()`.

Here the Module is compiled once and each call gets a fresh Store with an empty
WASI config, so memory is released and nothing leaks in. The parser emits
DuckDB-flavoured SQL and quotes the table name as one identifier — it cannot
be a subquery — so callers expose their source as a relation named RELATION
(a DuckDB view, or a CTE for live connections).

Column types must be passed (`fields`): without them the parser treats every
column as a number, so temporal filters and date drill/feature ops produce SQL
that compares timestamps with epoch-millisecond integers and fails.
"""
from __future__ import annotations

import atexit
import json
import os
import threading

RELATION = "sfs_src"
_module = None
_engine = None
_lock = threading.Lock()


class CompileError(ValueError):
    pass


def _load():
    global _module, _engine
    if _module is None:
        with _lock:
            if _module is None:
                import gw_dsl_parser                       # lazy — optional dep
                from wasmtime import Engine, Module
                path = os.path.join(os.path.dirname(gw_dsl_parser.__file__), "dsl_parser.wasm")
                _engine = Engine()
                _module = Module.from_file(_engine, path)
                atexit.register(_close)
    return _engine, _module


def _close():
    """Free the WASM module and engine while wasmtime still works. Left to the
    garbage collector, they are freed during interpreter teardown, after
    wasmtime's own globals are gone, and each prints "Exception ignored in
    Managed.__del__" to the log."""
    global _module, _engine
    for obj in (_module, _engine):
        try:
            obj.close()
        except Exception:                                  # noqa: BLE001
            pass
    _module = _engine = None


def field_meta(fields: list[dict], relation: str = RELATION) -> dict:
    """Parser metadata from Graphic Walker fields (+ optional `sqlType`).

    Undocumented format, found by probing the parser's error messages:
    {relation: [{"key": column, "type": string|number|datetime|datetime_tz|bool}]}
    """
    def kind(f):
        t = (f.get("sqlType") or "").upper()
        if f.get("semanticType") == "temporal":
            return "datetime_tz" if "TIME ZONE" in t or "TZ" in t else "datetime"
        if t.startswith("BOOL"):
            return "bool"
        return "number" if f.get("semanticType") == "quantitative" else "string"
    return {relation: [{"key": f["fid"], "type": kind(f)} for f in fields]}


def compile(payload: dict, fields: list[dict] | None = None, relation: str = RELATION) -> str:
    """Compile a Graphic Walker query payload to DuckDB SQL over `relation`."""
    from wasmtime import Linker, Store, WasiConfig

    engine, module = _load()
    store = Store(engine)
    store.set_wasi(WasiConfig())                          # no env, argv or stdio
    linker = Linker(engine)
    linker.define_wasi()
    ex = linker.instantiate(store, module).exports(store)
    mem = ex["memory"]

    args = [relation, json.dumps(payload)]
    if fields:
        args.append(json.dumps(field_meta(fields, relation)))
    ptrs = []
    for s in args:
        b = s.encode() + b"\0"
        p = ex["allocate"](store, len(b))
        mem.write(store, b, p)
        ptrs.append(p)
    fn = "parser_dsl_with_meta" if fields else "parser_dsl_with_table"
    out = ex[fn](store, *ptrs)
    data = mem.read(store, out, mem.data_len(store))
    sql = data[: data.index(0)].decode()
    # The parser reports failures as text rather than raising.
    if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
        raise CompileError(sql or "Graphic Walker query could not be compiled")
    sql = _floor_bin_count(sql)
    if fields:
        naive = {f["key"] for f in field_meta(fields, relation)[relation] if f["type"] == "datetime"}
        sql = _sargable_time_filters(sql, naive)
    return sql


def _sargable_time_filters(sql: str, naive_columns: set[str]) -> str:
    """Rewrite the parser's temporal range filter

        CAST(EXTRACT(EPOCH FROM ts) * 1000 AS BIGINT) BETWEEN <ms> AND <ms>

    to `ts BETWEEN TIMESTAMP '…' AND TIMESTAMP '…'`. Wrapping the column hides
    it from indexes and partition pruning, and the epoch expression does not
    transpile to every database (SQL Server has no EPOCH datepart; MySQL's
    UNIX_TIMESTAMP applies the session time zone). Equivalent only for
    timestamps stored without a zone, so other columns are left alone.
    """
    if "EXTRACT(EPOCH" not in sql or not naive_columns:
        return sql
    from datetime import datetime, timedelta, timezone

    import sqlglot
    from sqlglot import exp

    def ts(lit):
        ms = int(lit.this)
        t = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)
        # Millisecond precision, like the filter values: TIMESTAMP(3) becomes
        # DateTime64(3) / DATETIME2(3) / … so a range ending at .999 keeps its last second.
        return exp.cast(exp.Literal.string(t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]),
                        exp.DataType.build("TIMESTAMP(3)", dialect="duckdb"))

    def fix(node):
        if not isinstance(node, exp.Between):
            return node
        cast, low, high = node.this, node.args.get("low"), node.args.get("high")
        mul = cast.this if isinstance(cast, exp.Cast) else None
        ext = mul.this if isinstance(mul, exp.Mul) else None
        if not (isinstance(ext, exp.Extract) and ext.this.name.upper() == "EPOCH"
                and isinstance(ext.expression, exp.Column) and ext.expression.name in naive_columns
                and isinstance(mul.expression, exp.Literal) and mul.expression.this == "1000"
                and all(isinstance(v, exp.Literal) and not v.is_string for v in (low, high))):
            return node
        return exp.Between(this=ext.expression.copy(), low=ts(low), high=ts(high))
    return sqlglot.parse_one(sql, read="duckdb").transform(fix).sql(dialect="duckdb")


def _floor_bin_count(sql: str) -> str:
    """Fix the parser's binCount op, which emits `least((x - min) / step, n-1) + 1`
    without floor(), so every distinct value becomes its own "bin". The `bin` op
    floors correctly (`least(floor(...), n-1)`), so only an unfloored division
    as least()'s first argument is rewritten."""
    if "least((" not in sql:
        return sql
    import sqlglot
    from sqlglot import exp

    def fix(node):
        if isinstance(node, exp.Least) and isinstance(node.this.unnest(), exp.Div):
            node.set("this", exp.Floor(this=node.this))
        return node
    return sqlglot.parse_one(sql, read="duckdb").transform(fix).sql(dialect="duckdb")


def to_dialect(sql: str, dialect: str) -> str:
    """Transpile compiled (DuckDB) SQL to a target SQLGlot dialect."""
    if dialect == "duckdb":
        return sql
    import sqlglot
    return sqlglot.transpile(sql, read="duckdb", write=dialect)[0]
