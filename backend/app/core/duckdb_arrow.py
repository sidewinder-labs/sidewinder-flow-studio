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
