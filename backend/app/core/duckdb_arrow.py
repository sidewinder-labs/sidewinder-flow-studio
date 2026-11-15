"""DuckDB query result → pyarrow.Table, across DuckDB versions.

The Arrow fetch API has changed twice: 1.x has `.arrow()` (a Table, or from
1.5 a streaming RecordBatchReader) and `.fetch_arrow_table()`; 2.0 removed
both in favour of `.to_arrow_table()`. Use this instead of calling any of
them directly.

Run queries with `query_to_arrow(con, sql)`: DuckDB 2.0 warns on every Arrow
fetch from a `con.execute()` result, because that path copies the rows once
more; `con.sql()` gives the same table without the copy.
"""
import pyarrow as pa


def result_to_arrow(result) -> pa.Table:
    for name in ("to_arrow_table", "fetch_arrow_table", "arrow"):
        fn = getattr(result, name, None)
        if fn is not None:
            out = fn()
            return out if isinstance(out, pa.Table) else out.read_all()
    raise TypeError(f"Cannot fetch Arrow from {type(result).__name__}")


def query_to_arrow(con, sql: str) -> pa.Table:
    """Run `sql` (one or more statements) and return the last result as Arrow."""
    rel = con.sql(sql)
    return pa.table({}) if rel is None else result_to_arrow(rel)


def query_to_ipc(con, sql: str, path: str, batch_rows: int = 1_000_000) -> int | None:
    """Run `sql` and stream the last result into an Arrow IPC file at `path`,
    batch by batch, so the result never has to fit in memory. Returns the rows
    written, or None when the SQL produced no result (DDL only).

    Written to a temporary file and renamed, so a failed query never leaves a
    half-written frame behind."""
    import os
    rel = con.sql(sql)
    if rel is None:
        return None
    reader = None
    for name in ("to_arrow_reader", "fetch_record_batch", "fetch_arrow_reader"):
        fn = getattr(rel, name, None)
        if fn is not None:
            reader = fn(batch_rows)
            break
    if reader is None:                       # no streaming API: fetch, then write
        reader = result_to_arrow(rel).to_reader()
    import uuid
    tmp, n = f"{path}.{uuid.uuid4().hex[:12]}.tmp", 0     # own file: runs of one flow can overlap
    try:
        from app.core.arrow_files import ipc_writer
        from app.core.memory_guard import check
        with ipc_writer(tmp, reader.schema) as w:     # closed before the rename (Windows)
            for batch in reader:
                check()                      # memory budget: stop between batches
                w.write_batch(batch)
                n += batch.num_rows
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return n
