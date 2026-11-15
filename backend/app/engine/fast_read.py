"""fast_read: engine-independent, optionally parallel reads from a database.

One implementation behind three uses: the `fast_read()` helper (bound per engine,
returning pandas in pandas nodes and Polars in Polars nodes), `read_tables()`,
and the no-code Database read node, which outputs Arrow so any downstream
engine, DuckDB included, consumes it unchanged.

Paths, fastest first:

  * ConnectorX: Rust, straight into Arrow, no Python objects per row. With
    `partition_on`, the range of that column is split into slices read in
    parallel threads.
  * ClickHouse: its HTTP interface returns query results as ArrowStream;
    slices of `partition_on` are fetched in parallel threads. (ConnectorX only
    reaches ClickHouse through the MySQL protocol, which is often not exposed.)
  * SQLAlchemy: `pd.read_sql`, then Arrow. Used for dialects ConnectorX does not
    support, connections with connect_args a URI can't express, or when
    connectorx isn't installed. Partitioning is ignored there, with a note.

Every read returns (pyarrow.Table, info); info["path"] says which one ran.
"""
from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pyarrow as pa

log = logging.getLogger("sfs.fast_read")

BROKEN = "sfs-broken://"            # mirrors connection_service.BROKEN_URL_PREFIX
DEFAULT_PARTITIONS = 4

# SFS dialect → ConnectorX URI scheme, for the dialects ConnectorX reads.
_CX_SCHEME = {
    "postgresql": "postgresql",
    "redshift": "redshift",
    "mysql": "mysql",
    "mssql": "mssql",
    "oracle": "oracle",
    "sqlite": "sqlite",
}


class FastReadError(ValueError):
    pass


# ── Queries ──────────────────────────────────────────────────────────────────

def _qualify(name: str, schema: str | None) -> str:
    name = name.strip()
    return f"{schema}.{name}" if schema and "." not in name else name


def queries(*, table=None, tables=None, sql=None, schema=None) -> list[tuple[str, str | None]]:
    """[(query, source table or None)] for exactly one of table / tables / sql."""
    given = [k for k, v in (("table", table), ("tables", tables), ("sql", sql)) if v]
    if len(given) != 1:
        raise FastReadError("fast_read needs exactly one of table=, tables= or sql= "
                            f"(got {', '.join(given) or 'none'}).")
    if table:
        return [(f"SELECT * FROM {_qualify(table, schema)}", None)]
    if sql:
        return [(sql.strip().rstrip(";"), None)]
    if isinstance(tables, str):
        tables = [t for t in tables.split(",") if t.strip()]
    return [(f"SELECT * FROM {_qualify(t, schema)}", t.strip()) for t in tables]


# ── ConnectorX ───────────────────────────────────────────────────────────────

def connectorx_uri(url: str, dialect: str) -> str | None:
    """The ConnectorX URI for a SQLAlchemy URL, or None if ConnectorX can't read it."""
    import sqlalchemy as sa

    scheme = _CX_SCHEME.get(dialect)
    if not scheme:
        return None
    u = sa.engine.make_url(url)
    if scheme == "sqlite":
        if not u.database or u.database == ":memory:":
            return None
        from pathlib import Path
        return sqlite_uri(str(Path(u.database).resolve()))
    auth = ""
    if u.username:
        auth = quote(u.username, safe="")
        if u.password:
            auth += ":" + quote(str(u.password), safe="")
        auth += "@"
    host = u.host or "localhost"
    port = f":{u.port}" if u.port else ""
    db = f"/{quote(u.database, safe='')}" if u.database else ""
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in u.query.items()
                     if k not in ("driver",))                  # ODBC driver names mean nothing to ConnectorX
    return f"{scheme}://{auth}{host}{port}{db}" + (f"?{query}" if query else "")


def sqlite_uri(path: str) -> str:
    """ConnectorX's form for a SQLite file: the path percent-encoded, as its own
    ConnectionUrl builds it. A raw Windows path (C:\\…) would be read as a URL
    host and port."""
    return f"sqlite://{quote(path)}"


def _cx_available() -> bool:
    try:
        import connectorx  # noqa: F401
        return True
    except ImportError:
        return False


def _cx_read(uri: str, query: str, partition_on: str | None, rng, partitions: int) -> pa.Table:
    import connectorx as cx
    kwargs = {}
    if partition_on:
        kwargs = {"partition_on": partition_on, "partition_range": rng, "partition_num": partitions}
    out = cx.read_sql(uri, query, return_type="arrow", **kwargs)
    # ConnectorX may hand back a RecordBatchReader-like object on some versions
    return out if isinstance(out, pa.Table) else out.read_all()


# ── ClickHouse over HTTP, as Arrow ───────────────────────────────────────────

class _ClickHouse:
    def __init__(self, url: str):
        import sqlalchemy as sa
        u = sa.engine.make_url(url)
        proto = "https" if u.query.get("protocol") == "https" else "http"
        self.base = f"{proto}://{u.host}:{u.port or (8443 if proto == 'https' else 8123)}/"
        self.auth = (u.username or "default", str(u.password or ""))
        self.database = u.database or "default"

    def arrow(self, query: str) -> pa.Table:
        import httpx
        r = httpx.post(self.base, auth=self.auth, timeout=3600,
                       params={"database": self.database},
                       content=f"{query}\nFORMAT ArrowStream".encode())
        if r.status_code != 200:
            raise FastReadError(f"ClickHouse: {r.text.strip()[:500]}")
        if not r.content:
            return pa.table({})
        with pa.ipc.open_stream(r.content) as reader:
            return reader.read_all()


# ── Partitioning ─────────────────────────────────────────────────────────────

def _check_partition_column(min_max: pa.Table, col: str, integer_only: bool = False):
    """(lo, hi, nulls) of a numeric partition column; a clear error for anything else."""
    t = min_max.schema.field(0).type
    numeric = pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t)
    if not numeric:
        raise FastReadError(f"partition_on='{col}' must be a numeric column (it is {t}). "
                            "Pick an integer id or timestamp-as-number column, or leave it out.")
    if integer_only and not pa.types.is_integer(t):
        raise FastReadError(f"partition_on='{col}' must be an integer column for this database "
                            f"(it is {t}): ConnectorX splits integer ranges only.")
    lo, hi, nulls = (min_max.column(i)[0].as_py() for i in range(3))
    return lo, hi, int(nulls or 0)


def _slices(lo, hi, n: int) -> list[tuple]:
    """Up to n ranges covering [lo, hi]: [a, b) each, the last one closed [a, b]."""
    if lo is None or hi is None:
        return []
    n = max(1, int(n))
    step = (hi - lo) / n
    edges = [lo + step * i for i in range(1, n)]
    if isinstance(lo, int) and isinstance(hi, int):
        edges = [int(round(e)) for e in edges]
    edges = sorted({lo, hi, *(e for e in edges if lo < e < hi)})
    if len(edges) == 1:
        return [(lo, hi, True)]
    return [(edges[i], edges[i + 1], i == len(edges) - 2) for i in range(len(edges) - 1)]


# ── The read ─────────────────────────────────────────────────────────────────

def _plan(url: str, dialect: str, connect_args, partition_on, partitions):
    """Which path a read takes, its partition count and the notes for the user."""
    if not url:
        raise FastReadError("No such connection.")
    if url.startswith(BROKEN):
        raise FastReadError(url[len(BROKEN):])
    if url.startswith("adls://"):
        raise FastReadError("This is an Azure Data Lake storage connection: it has no SQL to read. "
                            "Use get_storage() with pd.read_parquet('abfs://…') instead.")
    partitions = int(partitions or DEFAULT_PARTITIONS) if partition_on else 1
    uri = connectorx_uri(url, dialect) if not connect_args else None
    if dialect == "clickhouse":
        path = "clickhouse-arrow"
    elif uri and _cx_available():
        path = "connectorx"
    else:
        path = "sqlalchemy"
    notes = []
    if path == "clickhouse-arrow" and partition_on:
        notes.append("ClickHouse already reads on all cores: partitioning helps only on a column "
                     "in the table's ORDER BY; otherwise each slice scans the whole table.")
    if path == "sqlalchemy":
        why = ("its connection options (connect_args) can't be passed to ConnectorX" if connect_args
               else "connectorx isn't installed" if uri
               else f"ConnectorX doesn't read {dialect or 'this database'}")
        notes.append(f"Read with SQLAlchemy because {why}.")
        if partition_on:
            notes.append("partition_on is ignored on this path.")
    return path, uri, partitions, notes


def _info(path, rows, qs, partition_on, partitions, notes) -> dict:
    info = {"path": path, "rows": rows, "queries": len(qs),
            "partitions": partitions if partition_on and path != "sqlalchemy" else 1}
    if notes:
        info["note"] = " ".join(notes)
    log.info("fast_read via %s: %s rows, %s queries, %s partitions",
             path, info["rows"], info["queries"], info["partitions"])
    return info


def read(url: str, dialect: str, connect_args: dict | None = None, *,
         table=None, tables=None, sql=None, schema=None,
         partition_on: str | None = None, partitions: int | None = None,
         source_col: str | None = "_source_table") -> tuple[pa.Table, dict]:
    """Read from a database connection into one Arrow table. See the module doc."""
    path, uri, partitions, notes = _plan(url, dialect, connect_args, partition_on, partitions)
    qs = queries(table=table, tables=tables, sql=sql, schema=schema)
    parts = []
    for query, source in qs:
        part = _read_one(path, url, uri, connect_args, query, partition_on, partitions)
        if source and source_col:
            part = part.append_column(source_col, pa.array([source] * part.num_rows, pa.string()))
        parts.append(part)
    result = parts[0] if len(parts) == 1 else pa.concat_tables(parts, promote_options="default")
    info = _info(path, result.num_rows, qs, partition_on, partitions, notes)
    record(info)
    return result, info


def _min_max_query(query: str, col: str) -> str:
    # NULLs fall outside every range, so they are counted and read separately.
    return (f"SELECT MIN({col}) AS lo, MAX({col}) AS hi, COUNT(*) - COUNT({col}) AS nulls "
            f"FROM ({query}) AS sfs_q")


def _nulls_query(query: str, col: str) -> str:
    return f"SELECT * FROM ({query}) AS sfs_q WHERE {col} IS NULL"


def _concat(tables: list[pa.Table]) -> pa.Table:
    tables = [t for t in tables if t.num_columns]
    if not tables:
        return pa.table({})
    return tables[0] if len(tables) == 1 else pa.concat_tables(tables, promote_options="default")


def _read_one(path, url, uri, connect_args, query, partition_on, partitions) -> pa.Table:
    if path == "connectorx":
        if not partition_on:
            return _cx_read(uri, query, None, None, 1)
        lo, hi, nulls = _check_partition_column(
            _cx_read(uri, _min_max_query(query, partition_on), None, None, 1), partition_on, integer_only=True)
        if lo is None or lo == hi:                          # nothing, or one value: no range to split
            return _cx_read(uri, query, None, None, 1)
        out = [_cx_read(uri, query, partition_on, (lo, hi), partitions)]
        if nulls:
            out.append(_cx_read(uri, _nulls_query(query, partition_on), None, None, 1))
        return _concat(out)

    if path == "clickhouse-arrow":
        ch = _ClickHouse(url)
        if not partition_on or partitions <= 1:
            return ch.arrow(query)
        lo, hi, nulls = _check_partition_column(ch.arrow(_min_max_query(query, partition_on)), partition_on)
        slices = _slices(lo, hi, partitions)
        if len(slices) <= 1 and not nulls:
            return ch.arrow(query)

        def one(s):
            if s is None:
                return ch.arrow(_nulls_query(query, partition_on))
            a, b, last = s
            op = "<=" if last else "<"
            return ch.arrow(f"SELECT * FROM ({query}) AS sfs_q "
                            f"WHERE {partition_on} >= {a!r} AND {partition_on} {op} {b!r}")
        jobs = slices + ([None] if nulls else [])
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            return _concat(list(pool.map(one, jobs)))

    import pandas as pd
    import sqlalchemy as sa
    eng = sa.create_engine(url, connect_args=connect_args) if connect_args else sa.create_engine(url)
    try:
        return pa.Table.from_pandas(pd.read_sql(query, eng), preserve_index=False)
    finally:
        eng.dispose()

# ── Streaming: the Database read node writes batches as they arrive ─────────
# The same reads as above, but batch by batch into the node's cache file, so
# a result larger than memory still loads. Falls back to the one-table read
# when the batches can't share one schema (several tables with different
# columns; SQLAlchemy chunks whose inferred types change).

STREAM_BATCH_ROWS = 100_000


class _Unstreamable(Exception):
    pass


def _cx_stream(uri: str, query: str, partition_on=None, rng=None, partitions: int = 1):
    import connectorx as cx
    kwargs = {"partition_on": partition_on, "partition_range": rng, "partition_num": partitions} if partition_on else {}
    yield from cx.read_sql(uri, query, return_type="arrow_stream", batch_size=STREAM_BATCH_ROWS, **kwargs)


class _Chunks(io.RawIOBase):
    """File-like view of an iterator of bytes chunks (an HTTP response body)."""
    def __init__(self, it):
        self.it, self.buf = it, b""

    def readable(self):
        return True

    def readinto(self, b):
        while not self.buf:
            try:
                self.buf = next(self.it)
            except StopIteration:
                return 0
        n = min(len(b), len(self.buf))
        b[:n], self.buf = self.buf[:n], self.buf[n:]
        return n


def _ch_stream(ch: "_ClickHouse", query: str):
    import httpx
    with httpx.Client(auth=ch.auth, timeout=3600) as client:
        with client.stream("POST", ch.base, params={"database": ch.database},
                           content=f"{query}\nFORMAT ArrowStream".encode()) as r:
            if r.status_code != 200:
                raise FastReadError(f"ClickHouse: {r.read().decode(errors='replace').strip()[:500]}")
            body = io.BufferedReader(_Chunks(r.iter_bytes(1 << 20)), 1 << 20)
            if not body.peek(1):
                return                                   # empty result: no stream at all
            yield from pa.ipc.open_stream(body)


def _parallel(gens: list, queue_size: int = 8):
    """Run several batch generators in threads; yield their batches as they
    come. A bounded queue keeps memory to a few batches."""
    import queue
    import threading
    q: queue.Queue = queue.Queue(maxsize=queue_size)
    stop = threading.Event()
    DONE = object()

    def work(g):
        try:
            for b in g:
                while not stop.is_set():
                    try:
                        q.put(b, timeout=0.2)
                        break
                    except queue.Full:
                        continue
                if stop.is_set():
                    return
        except BaseException as e:                       # noqa: BLE001 — handed to the consumer
            q.put(e)
        finally:
            q.put(DONE)

    threads = [threading.Thread(target=work, args=(g,), daemon=True) for g in gens]
    for t in threads:
        t.start()
    live = len(threads)
    try:
        while live:
            item = q.get()
            if item is DONE:
                live -= 1
            elif isinstance(item, BaseException):
                raise item
            else:
                yield item
    finally:
        stop.set()
        while live:                                      # unblock workers waiting on a full queue
            try:
                if q.get(timeout=0.5) is DONE:
                    live -= 1
            except Exception:
                break


def _sa_stream(url, connect_args, query: str):
    import pandas as pd
    import sqlalchemy as sa
    eng = sa.create_engine(url, connect_args=connect_args) if connect_args else sa.create_engine(url)
    try:
        with eng.connect().execution_options(stream_results=True) as conn:   # server-side cursor
            for chunk in pd.read_sql(sa.text(query), conn, chunksize=STREAM_BATCH_ROWS):
                yield from pa.Table.from_pandas(chunk, preserve_index=False).to_batches()
    finally:
        eng.dispose()


def _stream_one(path, url, uri, connect_args, query, partition_on, partitions):
    if path == "connectorx":
        if not partition_on:
            yield from _cx_stream(uri, query)
            return
        lo, hi, nulls = _check_partition_column(
            _cx_read(uri, _min_max_query(query, partition_on), None, None, 1), partition_on, integer_only=True)
        if lo is None or lo == hi:
            yield from _cx_stream(uri, query)
            return
        yield from _cx_stream(uri, query, partition_on, (lo, hi), partitions)
        if nulls:
            yield from _cx_stream(uri, _nulls_query(query, partition_on))
        return

    if path == "clickhouse-arrow":
        ch = _ClickHouse(url)
        if not partition_on or partitions <= 1:
            yield from _ch_stream(ch, query)
            return
        lo, hi, nulls = _check_partition_column(ch.arrow(_min_max_query(query, partition_on)), partition_on)
        slices = _slices(lo, hi, partitions)
        if len(slices) <= 1 and not nulls:
            yield from _ch_stream(ch, query)
            return
        gens = [_ch_stream(ch, f"SELECT * FROM ({query}) AS sfs_q WHERE {partition_on} >= {a!r} "
                               f"AND {partition_on} {'<=' if last else '<'} {b!r}") for a, b, last in slices]
        if nulls:
            gens.append(_ch_stream(ch, _nulls_query(query, partition_on)))
        yield from _parallel(gens)
        return

    yield from _sa_stream(url, connect_args, query)


def read_to_ipc(out_path: str, url: str, dialect: str, connect_args: dict | None = None, *,
                table=None, tables=None, sql=None, schema=None,
                partition_on: str | None = None, partitions: int | None = None,
                source_col: str | None = "_source_table") -> dict:
    """The same read as read(), written batch by batch to an Arrow IPC file
    at out_path, so the result never has to fit in memory. Returns the info."""
    import os
    import uuid

    from app.core.arrow_files import ipc_writer      # closed before the rename (Windows)
    from app.core.memory_guard import check
    path, uri, partitions, notes = _plan(url, dialect, connect_args, partition_on, partitions)
    qs = queries(table=table, tables=tables, sql=sql, schema=schema)
    tmp = f"{out_path}.{uuid.uuid4().hex[:12]}.tmp"
    writer, wschema, n = None, None, 0
    try:
        for query, source in qs:
            for batch in _stream_one(path, url, uri, connect_args, query, partition_on, partitions):
                check()                                      # memory budget: stop between batches
                if source and source_col:
                    batch = batch.append_column(source_col, pa.array([source] * batch.num_rows, pa.string()))
                if writer is None:
                    wschema = batch.schema
                    writer = ipc_writer(tmp, wschema)
                elif batch.schema != wschema:
                    try:
                        batch = batch.cast(wschema)
                    except (pa.ArrowInvalid, pa.ArrowNotImplementedError, ValueError) as e:
                        raise _Unstreamable(str(e)) from e
                writer.write_batch(batch)
                n += batch.num_rows
        if writer is None:                                   # no rows at all: same frame read() gives
            empty, _ = read(url, dialect, connect_args, table=table, tables=tables, sql=sql, schema=schema,
                            partition_on=partition_on, partitions=partitions, source_col=source_col)
            writer = ipc_writer(tmp, empty.schema)
            writer.write_table(empty)
            n = empty.num_rows
        writer.close()
        writer = None
        os.replace(tmp, out_path)
    except _Unstreamable as e:
        if writer is not None:
            writer.close()
        os.remove(tmp)
        log.info("fast_read: batches don't share one schema (%s); reading in one piece", e)
        table_, info = read(url, dialect, connect_args, table=table, tables=tables, sql=sql, schema=schema,
                            partition_on=partition_on, partitions=partitions, source_col=source_col)
        with ipc_writer(tmp, table_.schema) as w:
            w.write_table(table_)
        os.replace(tmp, out_path)
        return info
    except BaseException:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    info = _info(path, n, qs, partition_on, partitions, notes)
    info["streamed"] = True
    record(info)
    return info


# ── Which path each read in a node took, for the node's output ───────────────
# The executor opens a list per node; asyncio.to_thread copies the context into
# the worker thread, so reads land in their own node even with parallel branches.
import contextvars as _cv

_node_reads: _cv.ContextVar[list | None] = _cv.ContextVar("sfs_fast_reads", default=None)


def begin_node():
    return _node_reads.set([])


def end_node(token) -> list[dict]:
    reads = _node_reads.get() or []
    _node_reads.reset(token)
    return reads


def record(info: dict) -> None:
    reads = _node_reads.get()
    if reads is not None:
        reads.append(info)
