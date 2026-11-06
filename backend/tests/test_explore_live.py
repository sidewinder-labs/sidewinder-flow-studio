"""Explore Phase 2: live sources (table, sql), dialect handling, guardrails.

Unit tests run against SQLite (a real database and SQLAlchemy inspector, no
server). The integration tests at the bottom run against PostgreSQL and
ClickHouse when SFS_TEST_PG_URL / SFS_TEST_CH_URL are set, e.g.

    SFS_TEST_PG_URL=postgresql+psycopg2://user:pw@localhost:5432/employees
    SFS_TEST_CH_URL=clickhouse+http://user:pw@localhost:8123/default
"""
import os
from types import SimpleNamespace

import duckdb
import pytest
import sqlalchemy as sa

from app.explore import compiler, live
from app.explore.sources import QueryTimeout

AGG = {"workflow": [{"type": "view", "query": [{"op": "aggregate", "groupBy": ["g"], "measures": [
    {"field": "x", "agg": "sum", "asFieldKey": "s"}]}]}]}
MEDIAN = {"workflow": [{"type": "view", "query": [{"op": "aggregate", "groupBy": [], "measures": [
    {"field": "x", "agg": "median", "asFieldKey": "m"}]}]}]}
RAW = {"workflow": [{"type": "view", "query": [{"op": "raw", "fields": ["x"]}]}]}
TS_FIELDS = [{"fid": "ts", "semanticType": "temporal", "sqlType": "TIMESTAMP"},
             {"fid": "x", "semanticType": "quantitative", "sqlType": "BIGINT"}]


def ts_filter(lo_ms, hi_ms, col="ts"):
    return {"workflow": [
        {"type": "filter", "filters": [{"fid": col, "rule": {"type": "temporal range", "value": [lo_ms, hi_ms]}}]},
        {"type": "view", "query": [{"op": "aggregate", "groupBy": [], "measures": [
            {"field": "*", "agg": "count", "asFieldKey": "n"}]}]}]}


@pytest.fixture
def sqlite(tmp_path):
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'live.db'}")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE sales (g TEXT, x INTEGER)")
        c.exec_driver_sql("INSERT INTO sales VALUES " + ",".join(f"('{'ab'[i % 2]}', {i})" for i in range(100)))
        c.exec_driver_sql("CREATE VIEW big_sales AS SELECT * FROM sales WHERE x > 50")
    return eng


# ── SQL building ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dialect,cap_sql", [
    ("postgres", "LIMIT 11"), ("clickhouse", "LIMIT 11"), ("mysql", "LIMIT 11"),
    ("tsql", "SELECT TOP 11 *"), ("oracle", "FETCH FIRST 11 ROWS ONLY")])
def test_live_sql_keeps_cte_on_top_and_caps_per_dialect(dialect, cap_sql):
    sql = live.live_sql(compiler.compile(AGG), "SELECT * FROM t", dialect, 10)
    assert sql.split("\n")[0].startswith("WITH ") and "SELECT * FROM t" in sql
    assert cap_sql in sql


@pytest.mark.parametrize("dialect,name", [("postgres", '"sfs_src"'), ("snowflake", '"sfs_src"'),
                                          ("mysql", "`sfs_src`"), ("tsql", "[sfs_src]")])
def test_relation_name_quoted_like_its_references(dialect, name):
    sql = live.live_sql(compiler.compile(AGG), "SELECT 1", dialect, 10)
    assert sql.startswith(f"WITH {name} AS (") and f"FROM {name}" in sql


@pytest.mark.parametrize("dialect,want", [
    ("postgres", "PERCENTILE_CONT(0.5) WITHIN GROUP"), ("clickhouse", "quantileExactInclusive(0.5)"),
    ("trino", "APPROX_PERCENTILE(")])
def test_median_per_dialect(dialect, want):
    assert want in live.live_sql(compiler.compile(MEDIAN), "SELECT 1", dialect, 10)


@pytest.mark.parametrize("dialect", ["mysql", "tsql"])
def test_median_unsupported_is_a_clear_error(dialect):
    with pytest.raises(compiler.CompileError, match=f"Median isn't available as an aggregate on {dialect}"):
        live.live_sql(compiler.compile(MEDIAN), "SELECT 1", dialect, 10)


def test_table_relation_quotes_per_dialect():
    assert live.relation_sql("table", "postgres", table="employees.salary") == "SELECT * FROM employees.salary"
    assert live.relation_sql("table", "tsql", table="dbo.Order Lines") == "SELECT * FROM dbo.[Order Lines]"


# ── SQL source guard ──────────────────────────────────────────────────────────

def test_read_query_kept_verbatim():
    q = "SELECT a::int, b FROM t WHERE c = 'x;y'  ;  "
    assert live.check_read_query(q, "postgres") == "SELECT a::int, b FROM t WHERE c = 'x;y'"


@pytest.mark.parametrize("q", ["WITH a AS (SELECT 1 AS x) SELECT x FROM a", "SELECT 1 UNION ALL SELECT 2"])
def test_read_query_accepts_with_and_union(q):
    assert live.check_read_query(q, "postgres") == q


@pytest.mark.parametrize("q,msg", [
    ("DELETE FROM t", "not DELETE"), ("DROP TABLE t", "not DROP"), ("INSERT INTO t VALUES (1)", "not INSERT"),
    ("UPDATE t SET a = 1", "not UPDATE"), ("SELECT 1; DELETE FROM t", "single SELECT"),
    ("", "empty"), ("SELEC nonsense", "doesn't read as a SELECT")])
def test_read_query_rejects(q, msg):
    with pytest.raises(live.SourceError, match=msg):
        live.check_read_query(q, "postgres")


# ── date filters ──────────────────────────────────────────────────────────────

def test_temporal_filter_is_rewritten_to_column_range():
    sql = compiler.compile(ts_filter(1706745600000, 1709251199999), TS_FIELDS)
    assert "EXTRACT(EPOCH" not in sql
    assert "\"ts\" BETWEEN CAST('2024-02-01 00:00:00.000' AS TIMESTAMP(3)) AND CAST('2024-02-29 23:59:59.999'" in sql


def test_temporal_filter_rewrite_matches_original_semantics():
    con = duckdb.connect()
    con.execute("CREATE TABLE sfs_src AS SELECT TIMESTAMP '2024-01-15' + INTERVAL (i * 7) MINUTE AS ts, 1 AS x"
                " FROM range(20000) t(i)")
    lo, hi = 1706745600000, 1709251199999
    want = con.execute("SELECT count(*) FROM sfs_src WHERE CAST(EXTRACT(EPOCH FROM ts) * 1000 AS BIGINT)"
                       f" BETWEEN {lo} AND {hi}").fetchone()[0]
    assert con.execute(compiler.compile(ts_filter(lo, hi), TS_FIELDS)).fetchone()[0] == want > 0


def test_timezone_aware_columns_are_not_rewritten():
    fields = [{"fid": "ts", "semanticType": "temporal", "sqlType": "TIMESTAMP WITH TIME ZONE"}]
    assert "EXTRACT(EPOCH" in compiler.compile(ts_filter(0, 1000), fields)


@pytest.mark.parametrize("dialect,frag", [("tsql", "DATETIME2(3)"), ("mysql", "DATETIME(3)"), ("clickhouse", "DateTime(3)")])
def test_rewritten_filter_transpiles(dialect, frag):
    sql = live.live_sql(compiler.compile(ts_filter(0, 1000), TS_FIELDS), "SELECT 1", dialect, 10)
    assert frag in sql and "EPOCH" not in sql.upper()


# ── execution: SQLite as a live database ──────────────────────────────────────

def test_list_tables(sqlite):
    got = live.list_tables(sqlite, "sqlite")
    assert [(t["name"], t["kind"]) for t in got["tables"]] == [("sales", "table"), ("big_sales", "view")]
    assert got["truncated"] is False


def test_list_tables_limit(sqlite):
    assert live.list_tables(sqlite, "sqlite", limit=1) == {
        "tables": [{"schema": "main", "name": "sales", "kind": "table", "partitioned": False, "rows": None}],
        "truncated": True}


def test_table_schema_and_query(sqlite):
    sch = live.live_schema(sqlite, "sqlite", "table", table="sales")
    assert {f["fid"]: f["semanticType"] for f in sch["fields"]} == {"g": "nominal", "x": "quantitative"}
    r = live.live_query(sqlite, "sqlite", "table", AGG, sch["fields"], table="sales")
    assert {row["g"]: row["s"] for row in r.rows} == {"a": sum(range(0, 100, 2)), "b": sum(range(1, 100, 2))}
    assert r.sql.startswith('WITH "sfs_src" AS (') and not r.truncated


def test_sql_source_schema_and_query(sqlite):
    q = "SELECT g, x * 2 AS x FROM sales WHERE x < 10"
    sch = live.live_schema(sqlite, "sqlite", "sql", sql=q)
    assert [f["fid"] for f in sch["fields"]] == ["g", "x"] and sch["rows"] is None
    r = live.live_query(sqlite, "sqlite", "sql", AGG, sch["fields"], sql=q)
    assert {row["g"]: row["s"] for row in r.rows} == {"a": 2 * (0 + 2 + 4 + 6 + 8), "b": 2 * (1 + 3 + 5 + 7 + 9)}
    assert q in r.sql                                    # user SQL kept verbatim


def test_row_cap(sqlite):
    r = live.live_query(sqlite, "sqlite", "table", RAW, [{"fid": "x", "semanticType": "quantitative"}],
                        table="sales", cap=7)
    assert len(r.rows) == 7 and r.truncated


def test_client_side_timeout_where_database_has_none(sqlite, monkeypatch):
    import time as _t
    real = live.ThreadPoolExecutor

    class Slow(real):
        def submit(self, fn, *a, **k):
            return super().submit(lambda: (_t.sleep(1.5), fn(*a, **k))[1])
    monkeypatch.setattr(live, "ThreadPoolExecutor", Slow)
    with pytest.raises(QueryTimeout, match="stopped waiting after 0.3 s"):
        live._execute(sqlite, "sqlite", "SELECT 1", 0.3)


# ── timeouts and ClickHouse quirks, with a stand-in engine ────────────────────

class FakeConn:
    def __init__(self, log, fail_on=None):
        self.log, self.fail_on = log, fail_on

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def exec_driver_sql(self, sql):
        self.log.append(sql)
        if self.fail_on and self.fail_on[0] in sql:
            raise RuntimeError(self.fail_on[1])
        return SimpleNamespace(keys=lambda: ["b"], fetchall=lambda: [("[1.5,2.5]",)])


def fake_engine(log, fail_on=None):
    return SimpleNamespace(connect=lambda: FakeConn(log, fail_on))


def test_postgres_timeout_is_transaction_local():
    log = []
    live._execute(fake_engine(log), "postgres", "SELECT 1", 30)
    assert log == ["SET LOCAL statement_timeout = 30000", "SELECT 1"]


def test_clickhouse_timeout_rides_on_the_query():
    log = []
    live._execute(fake_engine(log), "clickhouse", "SELECT 1", 30)
    assert log == ["SELECT 1\nSETTINGS max_execution_time = 30"]


def test_clickhouse_readonly_user_falls_back_to_client_timeout():
    log = []
    eng = fake_engine(log, ("SETTINGS", "Code: 164. Cannot modify 'max_execution_time' setting in readonly mode"))
    assert live._execute(eng, "clickhouse", "SELECT 1", 30) == [{"b": "[1.5,2.5]"}]
    assert log[-1] == "SELECT 1"


@pytest.mark.parametrize("msg", [
    "canceling statement due to statement timeout", "Code: 159. DB::Exception: Timeout exceeded: elapsed 30 s. (TIMEOUT_EXCEEDED)",
    "Query execution was interrupted, maximum statement execution time exceeded"])
def test_database_timeouts_become_query_timeout(msg):
    eng = fake_engine([], ("SELECT", msg))
    with pytest.raises(QueryTimeout):
        live._execute(eng, "postgres", "SELECT 1", 30)


def test_readonly_error_is_not_mistaken_for_timeout():
    eng = fake_engine([], ("SELECT", "Cannot modify 'max_execution_time' setting in readonly mode"))
    with pytest.raises(RuntimeError, match="readonly"):
        live._execute(eng, "postgres", "SELECT 1", 30)


def test_clickhouse_array_columns_decoded():
    p = {"workflow": [
        {"type": "transform", "transform": [{"key": "b", "expression": {
            "op": "bin", "as": "b", "params": [{"type": "field", "value": "x"}], "num": 10}}]},
        {"type": "view", "query": [{"op": "aggregate", "groupBy": ["b"], "measures": [
            {"field": "*", "agg": "count", "asFieldKey": "n"}]}]}]}
    compiled = compiler.compile(p, [{"fid": "x", "semanticType": "quantitative"}])
    assert live._array_columns(compiled) == {"b"}
    rows = [{"b": "[1.5,2.5]", "n": 3}, {"b": "not json [", "n": 1}]
    live._decode_arrays(rows, {"b"})
    assert rows == [{"b": [1.5, 2.5], "n": 3}, {"b": "not json [", "n": 1}]


def test_engine_cache_reuses_and_rebuilds(tmp_path):
    a = live.engine_for("c", f"sqlite:///{tmp_path / 'a.db'}", None)
    assert live.engine_for("c", f"sqlite:///{tmp_path / 'a.db'}", None) is a
    assert live.engine_for("c", f"sqlite:///{tmp_path / 'b.db'}", None) is not a     # connection edited


# ── integration: real PostgreSQL / ClickHouse ─────────────────────────────────

PG, CH = os.environ.get("SFS_TEST_PG_URL"), os.environ.get("SFS_TEST_CH_URL")


@pytest.mark.skipif(not PG, reason="SFS_TEST_PG_URL not set")
def test_postgres_live_against_direct_sql():
    eng = sa.create_engine(PG)
    sch = live.live_schema(eng, "postgres", "table", table="employees.salary")
    assert sch["rows"] and sch["rows"] > 0
    r = live.live_query(eng, "postgres", "table", ts_filter(946684800000, 949363199999, "from_date"),
                        sch["fields"], table="employees.salary")
    with eng.connect() as c:
        want = c.exec_driver_sql("SELECT count(*) FROM employees.salary WHERE from_date >= '2000-01-01'"
                                 " AND from_date < '2000-02-01'").scalar()
    assert r.rows[0]["n"] == want
    med = live.live_query(eng, "postgres", "table", {"workflow": [{"type": "view", "query": [{
        "op": "aggregate", "groupBy": [], "measures": [{"field": "amount", "agg": "median", "asFieldKey": "m"}]}]}]},
        sch["fields"], table="employees.salary")
    with eng.connect() as c:
        assert med.rows[0]["m"] == c.exec_driver_sql(
            "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY amount) FROM employees.salary").scalar()


@pytest.mark.skipif(not PG, reason="SFS_TEST_PG_URL not set")
def test_postgres_statement_timeout_is_enforced():
    eng = sa.create_engine(PG)
    with pytest.raises(QueryTimeout):
        live._execute(eng, "postgres", "SELECT pg_sleep(3)", 1)
    with eng.connect() as c:                      # the pooled connection is clean again
        assert c.exec_driver_sql("SHOW statement_timeout").scalar() in ("0", "0ms")


@pytest.mark.skipif(not CH, reason="SFS_TEST_CH_URL not set")
def test_clickhouse_live_against_direct_sql():
    eng = sa.create_engine(CH)
    sch = live.live_schema(eng, "clickhouse", "table", table="default.nyc_yellow_tripdata")
    r = live.live_query(eng, "clickhouse", "table", ts_filter(1705276800000, 1705363199999, "tpep_pickup_datetime"),
                        sch["fields"], table="default.nyc_yellow_tripdata")
    with eng.connect() as c:
        want = c.exec_driver_sql("SELECT count() FROM default.nyc_yellow_tripdata"
                                 " WHERE toDate(tpep_pickup_datetime) = '2024-01-15'").scalar()
    assert r.rows[0]["n"] == want
    bins = live.live_query(eng, "clickhouse", "table", {"workflow": [
        {"type": "transform", "transform": [{"key": "b", "expression": {
            "op": "bin", "as": "b", "params": [{"type": "field", "value": "trip_distance"}], "num": 10}}]},
        {"type": "view", "query": [{"op": "aggregate", "groupBy": ["b"], "measures": [
            {"field": "*", "agg": "count", "asFieldKey": "n"}]}]}]}, sch["fields"], table="default.nyc_yellow_tripdata")
    assert all(isinstance(row["b"], list) and len(row["b"]) == 2 for row in bins.rows)
