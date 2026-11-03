"""
Frame cache — stores Arrow IPC tables on disk between node executions.

Keyed by (flow_id, node_id).  Using Arrow IPC gives us:
  - Fast read/write (~1 GB/s)
  - Lossless type preservation
  - Compact columnar storage
  - Direct conversion to/from pandas with zero copy

Future: swap disk-backed store for in-memory dict when running
interactively, keep disk for scheduled / long-running flows.
"""
import pyarrow as pa
import pyarrow.ipc as ipc
import pandas as pd
from pathlib import Path

from app.core.config import settings


def _cache_path(flow_id: str, node_id: str) -> Path:
    base = Path(settings.FRAME_CACHE_DIR) / flow_id
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{node_id}.arrow"


def store_frame(flow_id: str, node_id: str, df: pd.DataFrame) -> int:
    """Persist a DataFrame as Arrow IPC. Returns row count."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    path = _cache_path(flow_id, node_id)
    with ipc.new_file(str(path), table.schema) as writer:
        writer.write_table(table)
    return len(table)


def load_frame(flow_id: str, node_id: str) -> pd.DataFrame:
    """Load a cached Arrow IPC file back to a DataFrame."""
    path = _cache_path(flow_id, node_id)
    if not path.exists():
        raise FileNotFoundError(f"No cached frame for node {node_id} in flow {flow_id}")
    with ipc.open_file(str(path)) as reader:
        return reader.read_all().to_pandas()


def load_frame_arrow(flow_id: str, node_id: str) -> pa.Table:
    """Load a cached frame as a pyarrow Table — no pandas conversion.
    Used by the DuckDB engine, which queries Arrow directly (zero-copy)."""
    path = _cache_path(flow_id, node_id)
    if not path.exists():
        raise FileNotFoundError(f"No cached frame for node {node_id} in flow {flow_id}")
    with ipc.open_file(str(path)) as reader:
        return reader.read_all()


def store_frame_arrow(flow_id: str, node_id: str, table: pa.Table) -> int:
    """Persist a pyarrow Table directly — no pandas conversion.
    Used by the DuckDB engine to write results straight back to the cache."""
    path = _cache_path(flow_id, node_id)
    with ipc.new_file(str(path), table.schema) as writer:
        writer.write_table(table)
    return len(table)


def has_frame(flow_id: str, node_id: str) -> bool:
    return _cache_path(flow_id, node_id).exists()


def frame_dest_path(flow_id: str, node_id: str) -> str:
    """Path a node's output SHOULD be written to (parent dirs created).
    Unlike frame_path() this does not require the file to exist — it is for
    engines that stream their result straight to disk."""
    path = _cache_path(flow_id, node_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def frame_path(flow_id: str, node_id: str) -> str:
    """Filesystem path of a cached Arrow IPC frame — used by the Polars engine
    to pl.scan_ipc() lazily, so large frames never fully load until collect()."""
    path = _cache_path(flow_id, node_id)
    if not path.exists():
        raise FileNotFoundError(f"No cached frame for node {node_id} in flow {flow_id}")
    return str(path)


def load_frame_head(flow_id: str, node_id: str, limit: int) -> pd.DataFrame:
    """First `limit` rows only — reads just the record batches needed."""
    path = _cache_path(flow_id, node_id)
    if not path.exists():
        raise FileNotFoundError(f"No cached frame for node {node_id} in flow {flow_id}")
    with pa.memory_map(str(path), "rb") as src:
        reader = ipc.open_file(src)
        batches, n = [], 0
        for i in range(reader.num_record_batches):
            if n >= limit:
                break
            b = reader.get_batch(i)
            batches.append(b)
            n += b.num_rows
        table = pa.Table.from_batches(batches, schema=reader.schema)
    return table.slice(0, limit).to_pandas()


def clear_flow_cache(flow_id: str):
    """Remove all cached frames for a flow (e.g. on re-run from root)."""
    base = Path(settings.FRAME_CACHE_DIR) / flow_id
    if base.exists():
        for f in base.glob("*.arrow"):
            f.unlink()


def get_frame_meta(flow_id: str, node_id: str) -> dict | None:
    """Return {rows, columns, size_bytes} without loading full frame."""
    path = _cache_path(flow_id, node_id)
    if not path.exists():
        return None
    # Memory-mapped: counting rows touches batch headers, not the data.
    with pa.memory_map(str(path), "rb") as src:
        reader = ipc.open_file(src)
        schema = reader.schema
        rows = sum(reader.get_batch(i).num_rows for i in range(reader.num_record_batches))
    return {
        "rows": rows,
        "columns": len(schema),
        "column_names": schema.names,
        "size_bytes": path.stat().st_size,
    }


def delete_project_cache(project_id: str) -> None:
    """Remove every cached frame of a project (on project delete)."""
    import shutil
    shutil.rmtree(Path(settings.FRAME_CACHE_DIR) / project_id, ignore_errors=True)


def delete_node_frames(project_id: str, node_ids) -> int:
    """Remove the cached frames of these nodes (on flow delete); returns how
    many files went. Frames are keyed by project and node, not by flow."""
    base = Path(settings.FRAME_CACHE_DIR) / project_id
    n = 0
    for node_id in node_ids:
        f = base / f"{node_id}.arrow"
        if f.exists():
            f.unlink()
            n += 1
    return n
