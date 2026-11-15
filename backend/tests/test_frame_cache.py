"""Frame cache metadata and the Output panel's frame preview endpoint."""
import pyarrow as pa
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import execute as execute_api
from app.core import frame_cache
from app.core.config import settings


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path))
    # Several record batches, so the preview has to stop early.
    batches = [pa.record_batch({"i": list(range(k * 100, k * 100 + 100)), "s": ["x"] * 100}) for k in range(5)]
    frame_cache.store_frame_arrow("p", "n", pa.Table.from_batches(batches))


def test_frame_meta(cache):
    assert frame_cache.get_frame_meta("p", "n") == {
        "rows": 500, "columns": 2, "column_names": ["i", "s"],
        "size_bytes": frame_cache._cache_path("p", "n").stat().st_size}
    assert frame_cache.get_frame_meta("p", "missing") is None


def test_head_reads_only_what_it_shows(cache):
    df = frame_cache.load_frame_head("p", "n", 150)
    assert len(df) == 150 and df["i"].tolist() == list(range(150))
    assert len(frame_cache.load_frame_head("p", "n", 10_000)) == 500


def test_inspect_endpoint(cache):
    app = FastAPI()
    app.include_router(execute_api.router, prefix="/api/execute")
    d = TestClient(app).get("/api/execute/frame/p/n", params={"limit": 3}).json()
    assert d["total"] == 500 and [r["i"] for r in d["rows"]] == [0, 1, 2]
    assert [c["name"] for c in d["schema"]] == ["i", "s"] and d["schema"][0]["dtype"] == "int64"


def _open_handles(path):
    """Paths this process still holds open that match `path` (Linux only)."""
    import os
    out = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            if os.readlink(f"/proc/self/fd/{fd}") == str(path):
                out.append(fd)
        except OSError:
            continue
    return out


@pytest.mark.skipif(not __import__("os").path.isdir("/proc/self/fd"), reason="needs /proc")
def test_written_frames_leave_no_open_file(tmp_path, monkeypatch):
    """Windows can't rename or delete a file still open, so a frame streamed to
    a .tmp and renamed failed there (WinError 32) while Linux didn't notice.
    Every writer must have closed its file when it returns."""
    import duckdb
    from app.core.arrow_files import ipc_writer
    from app.core.duckdb_arrow import query_to_ipc

    t = pa.table({"a": [1, 2, 3]})
    with ipc_writer(tmp_path / "w.arrow", t.schema) as w:
        w.write_table(t)
    assert not _open_handles(tmp_path / "w.arrow")

    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path))
    frame_cache.store_frame_arrow("p", "n", t)
    assert not _open_handles(frame_cache._cache_path("p", "n"))

    # The moment that matters is the rename: Windows refuses it while the
    # .tmp is open, though the handle is gone by the time the function returns.
    import os
    real_replace, renamed = os.replace, []

    def checked_replace(src, dst):
        assert not _open_handles(src), f"{src} still open when renamed"
        renamed.append(src)
        real_replace(src, dst)
    monkeypatch.setattr(os, "replace", checked_replace)

    out = tmp_path / "duck.arrow"
    with duckdb.connect() as con:
        assert query_to_ipc(con, "SELECT range AS i FROM range(10)", str(out)) == 10
        assert query_to_ipc(con, "SELECT range AS i FROM range(5)", str(out)) == 5   # replaces the first
    assert len(renamed) == 2 and not _open_handles(out) and not list(tmp_path.glob("*.tmp"))
    assert pa.ipc.open_file(str(out)).read_all().num_rows == 5
