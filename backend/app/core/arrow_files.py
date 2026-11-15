"""Writing Arrow IPC files that are closed when the writer is.

`pa.ipc.new_file(path, schema)` opens the file itself and keeps it open after
the writer's close() — until the writer object is garbage-collected. Linux
doesn't mind; Windows refuses to rename or delete a file that is still open
(WinError 32), so a write-to-.tmp-then-rename fails there. Use ipc_writer()
instead: it owns the file and closes it with the writer.
"""
from __future__ import annotations

import pyarrow as pa


class ipc_writer:
    """An Arrow IPC file writer for `path`; use as a context manager, or call
    close(). write_batch / write_table as on pyarrow's writer."""

    def __init__(self, path, schema: pa.Schema):
        self._sink = pa.OSFile(str(path), "wb")
        try:
            self._writer = pa.ipc.new_file(self._sink, schema)
        except BaseException:
            self._sink.close()
            raise

    def write_batch(self, batch: pa.RecordBatch) -> None:
        self._writer.write_batch(batch)

    def write_table(self, table: pa.Table) -> None:
        self._writer.write_table(table)

    def close(self) -> None:
        if self._sink.closed:
            return
        try:
            self._writer.close()
        finally:
            self._sink.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()
