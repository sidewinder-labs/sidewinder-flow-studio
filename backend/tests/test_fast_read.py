"""fast_read: the helper, read_tables on top of it, and the Database read engine.

Runs on SQLite, which ConnectorX also reads, so the parallel path is exercised
without a database server. Set SFS_TEST_PG_URL (a SQLAlchemy URL) to also run
the PostgreSQL checks.
"""
import asyncio
import os

import pandas as pd
import polars as pl
import pyarrow as pa
import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.engine import executor, fast_read
from app.engine.fast_read import FastReadError, connectorx_uri, queries

pytest.importorskip("connectorx")


@pytest.fixture
def lite(tmp_path):
    url = f"sqlite:///{tmp_path / 'shop.db'}"
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE sales (id INTEGER, region TEXT, amount REAL)")
        c.exec_driver_sql("INSERT INTO sales VALUES " + ",".join(
            f"({i}, '{'NESW'[i % 4]}', {i * 1.5})" for i in range(1, 1001)))
        c.exec_driver_sql("CREATE TABLE sales_2026 (id INTEGER, region TEXT, amount REAL)")
        c.exec_driver_sql("INSERT INTO sales_2026 VALUES (2001, 'N', 9.0), (2002, 'S', 8.0)")
    return url


def ctx_for(url, dialect="sqlite", cargs=None):
    return executor.build_exec_context({"db": url}, {}, connect_args={"db": cargs} if cargs else {},
                                       dialects={"db": dialect})


# ── Pieces ───────────────────────────────────────────────────────────────────

def test_exactly_one_source_is_required():
    with pytest.raises(FastReadError, match="exactly one"):
        queries()
    with pytest.raises(FastReadError, match="table, sql"):
        queries(table="t", sql="SELECT 1")
    assert queries(table="t", schema="s") == [("SELECT * FROM s.t", None)]
    assert queries(table="s.t", schema="other") == [("SELECT * FROM s.t", None)]
    assert queries(tables="a, b") == [("SELECT * FROM a", "a"), ("SELECT * FROM b", "b")]
    assert queries(sql="SELECT 1;") == [("SELECT 1", None)]


def test_connectorx_uris():
    assert connectorx_uri("postgresql+psycopg://u:p%40ss@db:5432/shop", "postgresql") == \
        "postgresql://u:p%40ss@db:5432/shop"
    assert connectorx_uri("mssql+pyodbc://u:pw@sql:1433/dw?driver=ODBC+Driver+18&Encrypt=no", "mssql") == \
        "mssql://u:pw@sql:1433/dw?Encrypt=no"
    assert connectorx_uri("mysql+pymysql://u:pw@my/shop", "mysql") == "mysql://u:pw@my/shop"
    assert connectorx_uri("sqlite:////tmp/x.db", "sqlite") == "sqlite:///tmp/x.db"
    assert connectorx_uri("trino://u@t:8080/hive", "trino") is None          # not read by ConnectorX
    assert connectorx_uri("clickhouse+http://u:pw@ch:8123/default", "clickhouse") is None


# ── The helper ───────────────────────────────────────────────────────────────

def test_table_tables_and_sql_modes(lite):
    fr = ctx_for(lite)["fast_read"]
    t = fr("db", table="sales")
    assert isinstance(t, pd.DataFrame) and len(t) == 1000 and list(t.columns) == ["id", "region", "amount"]

    both = fr("db", tables=["sales", "sales_2026"])
    assert len(both) == 1002 and both["_source_table"].value_counts().to_dict() == {"sales": 1000, "sales_2026": 2}

    q = fr("db", sql="SELECT region, COUNT(*) AS n FROM sales GROUP BY region ORDER BY region")
    assert q.to_dict("list") == {"region": ["E", "N", "S", "W"], "n": [250, 250, 250, 250]}


@pytest.mark.parametrize("partitions", [2, 4, 8])
def test_partitioned_read_equals_unpartitioned(lite, partitions):
    arrow = ctx_for(lite)["_fast_read_arrow"]
    whole, info1 = arrow("db", sql="SELECT * FROM sales")
    parts, info = arrow("db", sql="SELECT * FROM sales", partition_on="id", partitions=partitions)
    assert info1["path"] == info["path"] == "connectorx" and info["partitions"] == partitions
    key = [("id", "ascending")]
    assert parts.sort_by(key).equals(whole.sort_by(key))


def test_partitioned_read_keeps_rows_with_a_null_key(lite):
    with sa.create_engine(lite).begin() as c:
        c.exec_driver_sql("INSERT INTO sales VALUES (NULL, 'N', 1.0), (NULL, 'S', 2.0)")
    parts, _ = ctx_for(lite)["_fast_read_arrow"]("db", table="sales", partition_on="id", partitions=4)
    assert parts.num_rows == 1002 and parts.column("id").null_count == 2


def test_connectorx_needs_an_integer_partition_column(lite):
    with pytest.raises(FastReadError, match="must be an integer column"):
        ctx_for(lite)["fast_read"]("db", table="sales", partition_on="amount")


def test_non_numeric_partition_column_is_rejected(lite):
    with pytest.raises(FastReadError, match="must be a numeric column"):
        ctx_for(lite)["fast_read"]("db", table="sales", partition_on="region")


def test_fallback_for_unsupported_dialect_and_connect_args(lite):
    arrow = ctx_for(lite, dialect="druid")["_fast_read_arrow"]      # a dialect ConnectorX doesn't read
    t, info = arrow("db", table="sales", partition_on="id")
    assert info["path"] == "sqlalchemy" and t.num_rows == 1000
    assert "doesn't read druid" in info["note"] and "partition_on is ignored" in info["note"]

    t, info = ctx_for(lite, cargs={"timeout": 5})["_fast_read_arrow"]("db", table="sales")
    assert info["path"] == "sqlalchemy" and "connect_args" in info["note"]


def test_broken_and_unknown_connections_raise_clear_errors(lite):
    broken = executor.build_exec_context({"db": "sfs-broken://Connection 'db' can't be decrypted"}, {},
                                         dialects={"db": "postgresql"})
    with pytest.raises(FastReadError, match="can't be decrypted"):
        broken["fast_read"]("db", table="t")
    with pytest.raises(ValueError, match="not found"):
        ctx_for(lite)["fast_read"]("nope", table="t")


def test_read_tables_keeps_its_signature_and_returns_pandas(lite):
    out = ctx_for(lite)["read_tables"]("db", ["sales", "sales_2026"], add_source_col="src")
    assert isinstance(out, pd.DataFrame) and len(out) == 1002 and set(out["src"]) == {"sales", "sales_2026"}


def test_same_line_returns_each_engines_native_type(lite, tmp_path):
    ctx = ctx_for(lite)
    line = 'df = fast_read("db", table="sales", partition_on="id")'
    pandas_out = executor.exec_processor(line, None, ctx, "")
    assert isinstance(pandas_out, pd.DataFrame) and len(pandas_out) == 1000
    polars_out = executor.exec_polars_processor(line + "\nassert isinstance(df, pl.DataFrame)", [], ctx, "")
    assert isinstance(polars_out, pa.Table) and polars_out.num_rows == 1000


# ── The Database read node, with DuckDB downstream ───────────────────────────

def test_database_read_node_feeds_duckdb(lite, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path / "frames"))
    async def quiet(*a, **k): pass
    monkeypatch.setattr(executor.ws_manager, "broadcast", quiet)
    node = lambda i, d: {"id": i, "type": "processor", "position": {"x": 0, "y": 0}, "data": {"label": i, **d}}
    edge = lambda s, t: {"id": f"{s}-{t}", "source": s, "target": t, "sourceHandle": "out", "targetHandle": "in"}
    flow = {"id": "f", "name": "f", "nodes": [
        {"id": "t", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"label": "Start", "run_mode": "manual"}},
        node("read", {"engine": "fast_read", "connection": "db", "mode": "tables",
                      "source": "sales, sales_2026", "partition_on": "id", "partitions": 4}),
        node("agg", {"engine": "duckdb", "code": "SELECT _source_table, COUNT(*) AS n FROM df GROUP BY 1 ORDER BY 1"}),
    ], "edges": [edge("t", "read"), edge("read", "agg")]}
    results = asyncio.run(executor.execute_flow("p", flow, {"db": lite}, {}, connection_dialects={"db": "sqlite"}))
    by = {r["node_id"]: r for r in results}
    assert all(r["status"] == "ok" for r in results), results
    assert by["read"]["reads"][0]["path"] == "connectorx" and by["read"]["reads"][0]["partitions"] == 4
    from app.core.frame_cache import load_frame_arrow
    assert load_frame_arrow("p", "agg").to_pylist() == [{"_source_table": "sales", "n": 1000},
                                                        {"_source_table": "sales_2026", "n": 2}]


def test_view_as_python_matches_the_node():
    spec = executor.get_engine_spec("fast_read")
    code = spec.to_code({"connection": "pg", "mode": "sql", "source": "SELECT 1", "schema": "",
                         "partition_on": "id", "partitions": 8}, [])
    assert "df = fast_read('pg', sql='SELECT 1', partition_on='id', partitions=8)" in code


# ── PostgreSQL (optional) ────────────────────────────────────────────────────

PG = os.environ.get("SFS_TEST_PG_URL")


@pytest.mark.skipif(not PG, reason="set SFS_TEST_PG_URL to run against PostgreSQL")
def test_postgresql_partitioned_equals_unpartitioned():
    arrow = executor.build_exec_context({"pg": PG}, {}, dialects={"pg": "postgresql"})["_fast_read_arrow"]
    whole, _ = arrow("pg", table="employees.employee")
    parts, info = arrow("pg", table="employees.employee", partition_on="id", partitions=8)
    assert info["path"] == "connectorx" and info["partitions"] == 8
    assert parts.sort_by("id").equals(whole.sort_by("id"))
