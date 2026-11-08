"""Streaming loads: fast_write sends data in batches as it is read, and DuckDB
nodes stream their result to the frame cache, so a load never has to fit in
memory. Runs on SQLite; set SFS_TEST_PG_URL (a SQLAlchemy URL to a scratch
database) to also run the PostgreSQL COPY checks.
"""
import asyncio
import os

import duckdb
import pandas as pd
import polars as pl
import pyarrow as pa
import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.engine import executor
from app.engine.executor import _arrow_batches


def frame(n=1000):
    return pd.DataFrame({"id": range(n), "name": [f"r{i}" for i in range(n)], "x": [i * 0.5 for i in range(n)]})


# ── Batching any input ───────────────────────────────────────────────────────

@pytest.mark.parametrize("make", [
    lambda d: d,                                               # pandas
    lambda d: pl.from_pandas(d),                               # Polars
    lambda d: pl.from_pandas(d).lazy(),                        # Polars lazy
    lambda d: pa.Table.from_pandas(d, preserve_index=False),   # Arrow table
    lambda d: pa.Table.from_pandas(d, preserve_index=False).to_reader(max_chunksize=333),  # Arrow stream
    lambda d: duckdb.connect().sql("SELECT * FROM d"),          # DuckDB relation
], ids=["pandas", "polars", "lazy", "arrow", "reader", "duckdb"])
def test_any_input_comes_out_as_bounded_batches_of_one_schema(make):
    d = frame()
    schema, batches = _arrow_batches(make(d), 128)
    got = list(batches)
    assert sum(b.num_rows for b in got) == 1000
    assert max(b.num_rows for b in got) <= 128
    assert all(b.schema == schema for b in got)
    assert schema.names == ["id", "name", "x"]


def test_empty_lazy_frame_still_gives_its_schema():
    schema, batches = _arrow_batches(pl.LazyFrame({"a": [1]}).filter(pl.col("a") > 5), 10)
    assert schema.names == ["a"] and list(batches) == []


# ── fast_write on SQLite (the batched to_sql path) ───────────────────────────

@pytest.fixture
def lite(tmp_path):
    url = f"sqlite:///{tmp_path / 'w.db'}"
    return url, executor.build_exec_context({"db": url}, {}, dialects={"db": "sqlite"})["fast_write"]


def count(url, table="t"):
    with sa.create_engine(url).connect() as c:
        return c.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar()


def test_lazy_frame_is_streamed_never_collected_whole(lite, monkeypatch):
    url, fast_write = lite
    def no_collect(*a, **k):
        raise AssertionError("fast_write collected the whole LazyFrame")
    monkeypatch.setattr(pl.LazyFrame, "collect", no_collect)
    assert fast_write(pl.from_pandas(frame()).lazy(), "db", "t", chunk_rows=100) == 1000
    assert count(url) == 1000


def test_append_replace_and_recreate(lite):
    url, fast_write = lite
    fast_write(frame(10), "db", "t", chunk_rows=3)
    fast_write(frame(10), "db", "t", chunk_rows=3)
    assert count(url) == 20
    assert fast_write(frame(7), "db", "t", mode="replace", chunk_rows=3) == 7
    assert count(url) == 7
    fast_write(pd.DataFrame({"other": [1, 2]}), "db", "t", mode="replace", recreate=True)
    with sa.create_engine(url).connect() as c:
        assert [r[1] for r in c.exec_driver_sql("PRAGMA table_info(t)")] == ["other"]


def failing_stream(n_ok=2):
    """An Arrow stream that fails after `n_ok` batches, like a source that
    breaks halfway through a load."""
    schema = pa.schema([("id", pa.int64()), ("name", pa.string()), ("x", pa.float64())])
    def gen():
        for i in range(n_ok):
            yield pa.record_batch([pa.array([i]), pa.array(["n"]), pa.array([0.0])], schema=schema)
        raise RuntimeError("source broke")
    return pa.RecordBatchReader.from_batches(schema, gen())


def test_a_load_that_fails_halfway_leaves_the_table_as_it_was(lite):
    url, fast_write = lite
    fast_write(frame(5), "db", "t")
    with pytest.raises(Exception, match="source broke"):
        fast_write(failing_stream(), "db", "t", mode="replace", chunk_rows=1)
    assert count(url) == 5


def test_replace_with_no_rows_empties_the_table(lite):
    url, fast_write = lite
    fast_write(frame(5), "db", "t")
    assert fast_write(frame(0), "db", "t", mode="replace") == 0
    assert count(url) == 0


# ── DuckDB node streams its result to the cache ──────────────────────────────

def test_duckdb_node_streams_to_the_cache_file(tmp_path):
    out = str(tmp_path / "out.arrow")
    res = executor.exec_duckdb_processor("SELECT range AS id FROM range(2500000)", [], output_path=out)
    assert isinstance(res, executor._Sunk)
    with pa.ipc.open_file(out) as r:
        assert r.num_record_batches > 1 and r.read_all().num_rows == 2500000
    assert not list(tmp_path.glob("*.tmp"))
    # DDL only: the cached frame is what the in-memory path returns (version-dependent)
    want = executor.exec_duckdb_processor("CREATE TABLE x (a INT)", [])
    res = executor.exec_duckdb_processor("CREATE TABLE x (a INT)", [], output_path=out)
    got = pa.table({}) if not isinstance(res, executor._Sunk) else pa.ipc.open_file(out).read_all()
    assert got.equals(want)


def test_failed_duckdb_query_leaves_no_partial_frame(tmp_path):
    out = tmp_path / "out.arrow"
    with pytest.raises(Exception):
        executor.exec_duckdb_processor("SELECT CAST('x' || range AS INT) FROM range(10)", [], output_path=str(out))
    assert not out.exists() and not list(tmp_path.glob("*.tmp"))


def test_duckdb_union_into_polars_fast_write_flow(lite, tmp_path, monkeypatch):
    """The flow shape that loads a folder of CSVs: DuckDB reads and unions,
    a Polars node writes with fast_write."""
    url, _ = lite
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path / "frames"))
    async def quiet(*a, **k): pass
    monkeypatch.setattr(executor.ws_manager, "broadcast", quiet)
    data = tmp_path / "csv"
    data.mkdir()
    for i in range(3):
        frame(400).assign(part=i).to_csv(data / f"p{i}.csv", index=False)
    node = lambda i, d: {"id": i, "type": "processor", "position": {"x": 0, "y": 0}, "data": {"label": i, **d}}  # noqa: E731
    edge = lambda s, t: {"id": f"{s}-{t}", "source": s, "target": t, "sourceHandle": "out", "targetHandle": "in"}  # noqa: E731
    flow = {"id": "f", "name": "f", "nodes": [
        {"id": "t", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"label": "Start", "run_mode": "manual"}},
        node("read", {"engine": "duckdb", "code": f"SELECT * FROM read_csv('{data}/*.csv', union_by_name=true, filename=true)"}),
        node("write", {"engine": "polars", "code": 'fast_write(df, "db", "loaded", mode="replace", chunk_rows=100)'}),
    ], "edges": [edge("t", "read"), edge("read", "write")]}
    results = asyncio.run(executor.execute_flow("p", flow, {"db": url}, {}, connection_dialects={"db": "sqlite"}))
    by = {r["node_id"]: r for r in results}
    assert all(r["status"] == "ok" for r in results), results
    assert by["read"]["rows_out"] == 1200 and by["write"]["rows_in"] == 1200
    assert count(url, "loaded") == 1200


# ── PostgreSQL COPY (optional) ───────────────────────────────────────────────

PG = os.environ.get("SFS_TEST_PG_URL")


@pytest.mark.skipif(not PG, reason="set SFS_TEST_PG_URL to run against PostgreSQL")
def test_postgresql_copy_streams_batches_and_is_one_transaction(monkeypatch):
    fast_write = executor.build_exec_context({"pg": PG}, {}, dialects={"pg": "postgresql"})["fast_write"]
    eng = sa.create_engine(PG)
    tbl = "sfs_stream_test.t"
    try:
        monkeypatch.setattr(pl.LazyFrame, "collect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("collected")))
        lf = pl.from_pandas(frame(5000)).lazy().with_columns(pl.lit(None, dtype=pl.Int64).alias("maybe"))
        assert fast_write(lf, "pg", tbl, mode="replace", chunk_rows=700) == 5000
        monkeypatch.undo()
        with eng.connect() as c:
            assert c.exec_driver_sql(f"SELECT COUNT(*), COUNT(maybe), SUM(id) FROM {tbl}").one() == (5000, 0, sum(range(5000)))
        with pytest.raises(Exception, match="source broke"):
            fast_write(failing_stream(), "pg", tbl, mode="replace", chunk_rows=1)
        with eng.connect() as c:
            assert c.exec_driver_sql(f"SELECT COUNT(*) FROM {tbl}").scalar() == 5000   # rolled back
    finally:
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA IF EXISTS sfs_stream_test CASCADE")
        eng.dispose()


def test_overlapping_runs_of_one_node_each_write_their_own_file(tmp_path):
    """A scheduled and a manual run of the same flow can write the same
    node's frame at once: neither may lose its file."""
    import threading
    out, errs = str(tmp_path / "n.arrow"), []
    def run():
        try:
            executor.exec_duckdb_processor("SELECT range AS id FROM range(3000000)", [], output_path=out)
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs
    with pa.ipc.open_file(out) as r:
        assert r.read_all().num_rows == 3000000
    assert not list(tmp_path.glob("*.tmp"))
