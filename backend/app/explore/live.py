"""Explore on live connections: the query runs in the connection's own database.

    table — a [schema.]table on an SFS connection
    sql   — a SELECT the user wrote, in the connection's dialect

Both are exposed to the compiled chart query as a CTE named compiler.RELATION.
The compiled query (DuckDB SQL) is transpiled with SQLGlot; the user's SQL is
kept verbatim, so nothing SQLGlot can't round-trip is ever rewritten.

Guardrails: a row cap, a per-dialect statement timeout (server-side where the
database supports one), and SQL sources limited to a single read query.
Credentials stay in the connection service — engines are built from
connection_maps() and cached here by URL.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from app.core.config import settings
from app.explore import compiler
from app.explore.sources import QueryTimeout, Result, gw_field

log = logging.getLogger("sfs.explore")



class SourceError(ValueError):
    """The source definition itself is unusable (not a database error)."""


# ── engines ───────────────────────────────────────────────────────────────────

_engines: dict[str, tuple[str, object]] = {}
_engines_lock = threading.Lock()


def engine_for(name: str, url: str, connect_args: dict | None):
    """One pooled engine per connection, rebuilt when its URL or options change
    (an edited connection). Chart interactions fire many small queries; a new
    engine per query would reconnect every time."""
    import sqlalchemy as sa
    key = url + json.dumps(connect_args or {}, sort_keys=True, default=str)
    with _engines_lock:
        hit = _engines.get(name)
        if hit and hit[0] == key:
            return hit[1]
        if hit:
            hit[1].dispose()
        eng = sa.create_engine(url, connect_args=connect_args or {}, pool_pre_ping=True)
        _engines[name] = (key, eng)
        return eng


# ── SQL building ──────────────────────────────────────────────────────────────

def _median(dialect: str):
    """Median as an exact aggregate in the target dialect — SQLGlot's generic
    transpile is invalid on several (PERCENTILE_CONT(x, 0.5) on Trino/MySQL)."""
    from sqlglot import exp

    if dialect == "clickhouse":
        # median() is a sampled estimate; quantileExactInclusive matches DuckDB's
        # exact, interpolating median.
        return lambda n: exp.ParameterizedAgg(this="quantileExactInclusive",
                                              expressions=[exp.Literal.number(0.5)], params=[n.this])
    if dialect == "trino":
        # Trino has no exact percentile aggregate.
        return lambda n: exp.Anonymous(this="approx_percentile", expressions=[n.this, exp.Literal.number(0.5)])
    if dialect in ("mysql", "tsql", "starrocks", "doris"):
        def unsupported(n):
            raise compiler.CompileError(f"Median isn't available as an aggregate on {dialect} — use mean or another measure")
        return unsupported
    return None


def _body(compiled: str, dialect: str):
    """Compiled chart query as a SQLGlot tree, with dialect fixes applied."""
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(compiled, read="duckdb")
    med = _median(dialect)
    if med:
        tree = tree.transform(lambda n: med(n) if isinstance(n, exp.Median) else n)
    return tree


def relation_sql(kind: str, dialect: str, table: str | None = None, sql: str | None = None) -> str:
    """The SQL that defines RELATION for a live source, in the target dialect."""
    if kind == "table":
        from sqlglot import exp
        if not table:
            raise SourceError("No table given")
        return exp.select("*").from_(exp.to_table(table, dialect=dialect)).sql(dialect=dialect)
    if kind == "sql":
        return check_read_query(sql or "", dialect)
    raise SourceError(f"Unknown live source kind '{kind}'")


def check_read_query(sql: str, dialect: str) -> str:
    """A SQL source must be exactly one read query. The text is returned as
    written (minus a trailing semicolon) — it is only parsed to check."""
    import sqlglot
    from sqlglot import exp

    text = sql.strip().rstrip(";").strip()
    if not text:
        raise SourceError("The SQL query is empty")
    try:
        trees = [t for t in sqlglot.parse(text, read=dialect) if t is not None]
    except sqlglot.errors.ParseError as exc:
        raise SourceError(f"Could not parse the SQL: {str(exc).splitlines()[0]}")
    if len(trees) != 1:
        raise SourceError("Use a single SELECT statement")
    if not isinstance(trees[0], exp.Query):
        kind = trees[0].key.upper()
        if isinstance(trees[0], (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
                                 exp.Alter, exp.TruncateTable, exp.Command)):
            raise SourceError(f"Only SELECT queries can be explored, not {kind}")
        raise SourceError("This doesn't read as a SELECT query")
    return text


def live_sql(compiled: str, relation: str, dialect: str, cap: int | None, pretty: bool = False) -> str:
    """WITH RELATION AS (<relation>) SELECT * FROM (<chart query>) LIMIT cap+1.

    The CTE stays at the top level (SQL Server does not allow WITH inside a
    derived table); the row cap wraps only the chart query, rendered per
    dialect (LIMIT / TOP / FETCH FIRST). cap=None leaves the query uncapped
    (generated flow nodes); pretty=True lays it out for people to read.
    """
    from sqlglot import exp

    body = _body(compiled, dialect)
    if cap is not None:
        body = exp.select("*").from_(body.subquery("sfs_capped")).limit(cap + 1)
    out = body.sql(dialect=dialect, pretty=pretty)
    # Quoted like the chart query's references to it: Snowflake and Oracle
    # upper-case unquoted names, which would make the two differ.
    name = exp.to_identifier(compiler.RELATION, quoted=True).sql(dialect=dialect)
    cte = f"{name} AS (\n{relation}\n)"
    if out.upper().startswith("WITH "):              # chart query brought its own CTEs
        return f"WITH {cte}, {out[5:]}"
    return f"WITH {cte}\n{out}"


# ── timeouts ──────────────────────────────────────────────────────────────────

# What each database says when its own statement timeout fires. Kept to the
# exact phrases: a looser match (e.g. "max_execution_time") also catches
# "cannot modify max_execution_time in readonly mode".
_TIMEOUT_MARKERS = (
    "canceling statement due to statement timeout",      # PostgreSQL, Redshift
    "timeout_exceeded",                                  # ClickHouse (code 159)
    "maximum statement execution time exceeded",         # MySQL
    "reached its statement or warehouse timeout",        # Snowflake
    "exceeded_time_limit", "exceeded maximum time limit",  # Trino
)
_SERVER_TIMEOUT = ("postgres", "redshift", "mysql", "clickhouse", "snowflake", "trino")


def _execute(engine, dialect: str, sql: str, timeout: float) -> list[dict]:
    """Run with a server-side timeout where the database has one; elsewhere stop
    waiting after `timeout` (the database may finish the query on its own)."""
    secs = max(1, int(timeout))

    def run(text: str, set_timeout: bool) -> list[dict]:
        with engine.connect() as c:
            if set_timeout and dialect == "postgres":
                # LOCAL: ends with the transaction, which the pool rolls back on
                # return — nothing to reset, even after a cancelled query.
                c.exec_driver_sql(f"SET LOCAL statement_timeout = {secs * 1000}")
            elif set_timeout and dialect == "redshift":
                c.exec_driver_sql(f"SET statement_timeout = {secs * 1000}")
            elif set_timeout and dialect == "mysql":
                c.exec_driver_sql(f"SET SESSION max_execution_time = {secs * 1000}")
            elif set_timeout and dialect == "snowflake":
                c.exec_driver_sql(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {secs}")
            elif set_timeout and dialect == "trino":
                c.exec_driver_sql(f"SET SESSION query_max_execution_time = '{secs}s'")
            try:
                # exec_driver_sql: no bind-parameter parsing, so `::` casts survive
                cur = c.exec_driver_sql(text)
                cols = list(cur.keys())
                return [dict(zip(cols, r)) for r in cur.fetchall()]
            finally:
                if set_timeout and dialect == "redshift":
                    # Pooled connection: clear the session setting. A timed-out
                    # query leaves the transaction aborted, so roll back first,
                    # and never let cleanup hide the original error.
                    try:
                        c.rollback()
                        c.exec_driver_sql("RESET statement_timeout")
                    except Exception:
                        pass

    def waited(text: str) -> list[dict]:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            return pool.submit(run, text, False).result(timeout=timeout)
        except FutureTimeout:
            raise QueryTimeout(f"Query stopped waiting after {timeout:g} s — filter or aggregate further")
        finally:
            pool.shutdown(wait=False)

    try:
        if dialect == "clickhouse":
            try:
                return run(f"{sql}\nSETTINGS max_execution_time = {secs}", False)
            except Exception as exc:
                # readonly=1 users may not change any setting, this one included.
                if "readonly" not in str(exc).lower():
                    raise
                log.info("explore: ClickHouse user is readonly=1; timing out client-side")
                return waited(sql)
        if dialect in _SERVER_TIMEOUT:
            return run(sql, True)
        return waited(sql)
    except QueryTimeout:
        raise
    except Exception as exc:
        if any(m in str(exc).lower() for m in _TIMEOUT_MARKERS):
            raise QueryTimeout(f"Query stopped after {timeout:g} s — filter or aggregate further")
        raise


def _array_columns(compiled: str) -> set[str]:
    """Output columns the chart query builds as arrays (bin ranges)."""
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(compiled, read="duckdb")
    return {p.alias_or_name for p in tree.expressions if isinstance(p.unalias(), exp.Array)}


def _decode_arrays(rows: list[dict], cols: set[str]) -> None:
    """ClickHouse over HTTP returns arrays as text ('[1.5,2.5]'); Graphic Walker
    needs real arrays to draw bins."""
    for r in rows:
        for c in cols:
            v = r.get(c)
            if isinstance(v, str) and v.startswith("["):
                try:
                    r[c] = json.loads(v)
                except ValueError:
                    pass


# ── schema, query, listing ────────────────────────────────────────────────────

def _arrow_sql_type(t) -> str:
    import pyarrow.types as at
    if at.is_timestamp(t):
        return "TIMESTAMP WITH TIME ZONE" if t.tz else "TIMESTAMP"
    if at.is_date(t):
        return "DATE"
    if at.is_boolean(t):
        return "BOOLEAN"
    if at.is_integer(t):
        return "BIGINT"
    if at.is_floating(t) or at.is_decimal(t):
        return "DOUBLE"
    return "VARCHAR"


def live_schema(engine, dialect: str, kind: str, table: str | None = None, sql: str | None = None) -> dict:
    """Columns of a live source. Tables come from the database catalog; a SQL
    source is sampled (types inferred from up to 200 rows)."""
    import sqlalchemy as sa

    if kind == "table":
        schema, _, name = (table or "").rpartition(".")
        cols = sa.inspect(engine).get_columns(name, schema=schema or None)
        return {"fields": [gw_field(c["name"], str(c["type"])) for c in cols],
                "rows": estimate_rows(engine, dialect, schema or None, name)}
    import pandas as pd
    import pyarrow as pa

    rel = relation_sql("sql", dialect, sql=sql)
    from sqlglot import exp
    probe = exp.select("*").from_(exp.to_table("sfs_q")).limit(200).sql(dialect=dialect)
    rows = _execute(engine, dialect, f"WITH sfs_q AS (\n{rel}\n)\n{probe}", settings.EXPLORE_TIMEOUT_S)
    df = pd.DataFrame(rows)
    tbl = pa.Table.from_pandas(df, preserve_index=False) if len(df.columns) else pa.table({})
    return {"fields": [gw_field(f.name, _arrow_sql_type(f.type)) for f in tbl.schema], "rows": None}


def live_query(engine, dialect: str, kind: str, payload: dict, fields: list[dict],
               table: str | None = None, sql: str | None = None,
               cap: int | None = None, timeout: float | None = None) -> Result:
    cap = cap or settings.EXPLORE_ROW_CAP
    timeout = timeout or settings.EXPLORE_TIMEOUT_S
    t0 = time.perf_counter()
    compiled = compiler.compile(payload, fields)
    final = live_sql(compiled, relation_sql(kind, dialect, table=table, sql=sql), dialect, cap)
    t1 = time.perf_counter()
    rows = _execute(engine, dialect, final, timeout)
    if dialect == "clickhouse":
        _decode_arrays(rows, _array_columns(compiled))
    t2 = time.perf_counter()
    log.info("explore live %s %.0f ms: %s", table or "sql", (t2 - t1) * 1000, final)
    return Result(rows[:cap], final, (t1 - t0) * 1000, (t2 - t1) * 1000, len(rows) > cap)


_ROW_ESTIMATES = {
    # One cheap catalog query per dialect: planner statistics, not COUNT(*).
    "postgres": "SELECT n.nspname, c.relname, c.reltuples::bigint FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','m','p')",
    "redshift": "SELECT \"schema\", \"table\", tbl_rows FROM svv_table_info",
    "clickhouse": "SELECT database, name, total_rows FROM system.tables",
    "mysql": "SELECT table_schema, table_name, table_rows FROM information_schema.tables",
}


def _estimates(engine, dialect: str) -> dict[tuple, int]:
    q = _ROW_ESTIMATES.get(dialect)
    if not q:
        return {}
    try:
        with engine.connect() as c:
            return {(s, t): int(n) for s, t, n in c.exec_driver_sql(q).fetchall() if n is not None and n >= 0}
    except Exception as exc:          # no catalog access with read-only credentials
        log.info("explore: no row estimates on %s: %s", dialect, str(exc).splitlines()[0][:200])
        return {}


def estimate_rows(engine, dialect: str, schema: str | None, name: str) -> int | None:
    est = _estimates(engine, dialect)
    if schema:
        return est.get((schema, name))
    hits = [n for (s, t), n in est.items() if t == name]
    return hits[0] if len(hits) == 1 else None


def list_tables(engine, dialect: str, limit: int = 2000, settings: dict | None = None) -> dict:
    """Every visible table and view of a live connection (see catalog.objects)."""
    from app.explore import catalog
    from app.services.connection_service import BROWSER_DEFAULTS
    return catalog.objects(engine, dialect, settings or BROWSER_DEFAULTS, limit=limit)
