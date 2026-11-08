"""How much memory SFS uses, and how much it may use — without psutil.

process_bytes(): memory the backend process holds that the OS cannot simply
drop: anonymous RSS on Linux (memory-mapped cache files are file pages the OS
can evict, so they don't count), private bytes on Windows.

total_bytes(): physical memory, or the container's limit when lower (Docker
mem_limit / cgroup memory.max).

Both return None where they can't be measured; callers then do nothing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_UNITS = {"": 1, "B": 1, "KB": 1 << 10, "MB": 1 << 20, "GB": 1 << 30, "TB": 1 << 40,
          "KIB": 1 << 10, "MIB": 1 << 20, "GIB": 1 << 30, "TIB": 1 << 40}


def parse_size(text: str) -> int:
    """'12GB', '512 MB', '1.5gb' → bytes. Raises ValueError otherwise."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*", str(text))
    if not m or m.group(2).upper() not in _UNITS:
        raise ValueError(f"'{text}' is not a size: use e.g. 512MB or 12GB")
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def fmt_size(n: int | None) -> str:
    if n is None:
        return "unknown"
    for unit, size in (("TB", 1 << 40), ("GB", 1 << 30), ("MB", 1 << 20)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def process_bytes() -> int | None:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("RssAnon:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            return None
        return None
    if sys.platform == "win32":
        return _win_private_bytes()
    return None


def total_bytes() -> int | None:
    total = None
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                    break
        except OSError:
            pass
        limit = _cgroup_limit()
        if limit and (total is None or limit < total):
            total = limit
    elif sys.platform == "win32":
        total = _win_total()
    return total


def _cgroup_limit() -> int | None:
    for f in ("/sys/fs/cgroup/memory.max",                      # cgroup v2
              "/sys/fs/cgroup/memory/memory.limit_in_bytes"):   # cgroup v1
        try:
            v = Path(f).read_text().strip()
        except OSError:
            continue
        if v.isdigit() and int(v) < (1 << 60):                  # "max" or a huge number = no limit
            return int(v)
    return None


def release() -> None:
    """Hand freed memory back to the OS after a run: Arrow's pool and, on
    Linux, glibc's heap (otherwise a finished run can look like it still
    holds everything it used)."""
    try:
        import pyarrow as pa
        pa.default_memory_pool().release_unused()
    except Exception:
        pass
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass


# ── Windows ──────────────────────────────────────────────────────────────────

def _win_private_bytes() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        proc = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(proc, ctypes.byref(c), c.cb):
            return int(c.PrivateUsage)
    except Exception:
        pass
    return None


def _win_total() -> int | None:
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return int(m.ullTotalPhys)
    except Exception:
        pass
    return None
