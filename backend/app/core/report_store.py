"""
Report & persist store.

Two DuckDB-file-backed facilities:

1. REPORTS — Report nodes publish their input frame (plus optional
   Streamlit code) into reports.duckdb. The optional Streamlit viewer app
   mounts the same directory and reads it (single-writer / many-readers:
   the viewer connects read_only).

2. PERSIST — right-click "Persist" on a processor checkpoints its output to
   persist.duckdb (or a remote DB via the executor). "Resume from persisted"
   hydrates the frame cache from here so heavy upstream queries can be skipped
   across runs — even across days/restarts.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pyarrow as pa

from app.core.config import settings


def _connect_rw(path: str, retries: int = 8, delay: float = 0.5):
    """Open a read-write DuckDB connection, retrying briefly if a reader
    (report viewer) or OneDrive sync momentarily holds the file (Windows)."""
    import duckdb
    last: Exception | None = None
    for _ in range(retries):
        try:
            return duckdb.connect(path)
        except Exception as e:
            last = e
            time.sleep(delay)
    raise IOError(
        f"Could not open {path} for writing after {retries} attempts. "
        f"A report viewer may be holding it open, or the file is inside a "
        f"synced folder (OneDrive/Dropbox) — consider moving REPORTS_DIR "
        f"outside synced directories. Last error: {last}"
    )


def sanitize_table(name: str) -> str:
    """Make a safe DuckDB/SQL table name from a node label or report name."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip()).strip("_").lower()
    if not s:
        s = "unnamed"
    if s[0].isdigit():
        s = "t_" + s
    return s[:63]


def _reports_db() -> str:
    d = Path(settings.REPORTS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "reports.duckdb")


def _persist_db() -> str:
    p = Path(settings.PERSIST_DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


# ── Reports ───────────────────────────────────────────────────────────────────

def publish_report(name: str, table: pa.Table, kind: str, code: str = "") -> str:
    """Write the frame as a table + register metadata. Returns the table name."""
    import duckdb  # lazy

    tbl = sanitize_table(name)
    con = _connect_rw(_reports_db())
    try:
        con.register("_incoming", table)
        con.execute(f'CREATE OR REPLACE TABLE "{tbl}" AS SELECT * FROM _incoming')
        con.execute("""
            CREATE TABLE IF NOT EXISTS _reports (
                name VARCHAR PRIMARY KEY, kind VARCHAR, code VARCHAR,
                row_count BIGINT, updated_at DOUBLE
            )
        """)
        con.execute(
            "INSERT OR REPLACE INTO _reports VALUES (?, ?, ?, ?, ?)",
            [tbl, kind, code or "", table.num_rows, time.time()],
        )
    finally:
        con.close()
    return tbl


def flush_report(name: str) -> None:
    import duckdb
    tbl = sanitize_table(name)
    con = _connect_rw(_reports_db())
    try:
        con.execute(f'DROP TABLE IF EXISTS "{tbl}"')
        con.execute("DELETE FROM _reports WHERE name = ?", [tbl])
    except Exception:
        pass
    finally:
        con.close()


# ── Persist (checkpoints) ─────────────────────────────────────────────────────

def persist_frame(table_name: str, table: pa.Table) -> int:
    import duckdb
    tbl = sanitize_table(table_name)
    con = _connect_rw(_persist_db())
    try:
        con.register("_incoming", table)
        con.execute(f'CREATE OR REPLACE TABLE "{tbl}" AS SELECT * FROM _incoming')
    finally:
        con.close()
    return table.num_rows


def load_persist(table_name: str) -> pa.Table:
    import duckdb
    tbl = sanitize_table(table_name)
    con = duckdb.connect(_persist_db(), read_only=True)
    try:
        from app.core.duckdb_arrow import query_to_arrow
        return query_to_arrow(con, f'SELECT * FROM "{tbl}"')
    finally:
        con.close()


def has_persist(table_name: str) -> bool:
    import duckdb
    tbl = sanitize_table(table_name)
    if not Path(_persist_db()).exists():
        return False
    con = duckdb.connect(_persist_db(), read_only=True)
    try:
        r = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [tbl]
        ).fetchone()
        return r is not None
    except Exception:
        return False
    finally:
        con.close()


def drop_persist(table_name: str) -> None:
    import duckdb
    tbl = sanitize_table(table_name)
    con = _connect_rw(_persist_db())
    try:
        con.execute(f'DROP TABLE IF EXISTS "{tbl}"')
    finally:
        con.close()
