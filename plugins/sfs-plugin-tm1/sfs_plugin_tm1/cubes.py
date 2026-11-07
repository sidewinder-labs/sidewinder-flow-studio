"""Reading TM1 cubes into frames and loading frames into cubes.

Everything here takes an open TM1Service (`tm1`), so tests pass a fake one.

Load modes:
  replace_slice  zero the slice the frame covers (the cross product of its
                 distinct elements per dimension), then write. Re-running a
                 load gives the same result, so a retried flow is safe.
  add            add the values to what the cells hold.
  overwrite      write the given cells only; other cells keep their values.

Replace slice zeroes through ViewZeroOut when the TM1 user is a data and ops
admin (TM1 11.7+); otherwise it reads the slice and writes zeros.

TM1 element names are case- and space-insensitive ("North America" ==
"northamerica"), so existence checks compare them that way.
"""
from __future__ import annotations

import time

MODES = ("replace_slice", "add", "overwrite")


def _key(name) -> str:
    return str(name).replace(" ", "").lower()


# ── Read ──────────────────────────────────────────────────────────────────────

def read(tm1, *, mode: str = "mdx", mdx: str = "", cube: str = "", view: str = "",
         private: bool = False):
    """One row per cell: a column per dimension, then `value`."""
    if mode == "mdx":
        if not (mdx or "").strip():
            raise ValueError("TM1 read: write an MDX query, or switch Read to a cube view.")
        df = tm1.cells.execute_mdx_dataframe(mdx)
    elif mode == "view":
        if not cube or not view:
            raise ValueError("TM1 read: name the cube and the view.")
        df = tm1.cells.execute_view_dataframe(cube_name=cube, view_name=view, private=private)
    else:
        raise ValueError(f"TM1 read: unknown mode '{mode}' (mdx or view)")
    return df.rename(columns={"Value": "value"})


# ── Load ──────────────────────────────────────────────────────────────────────

def parse_mapping(text: str) -> dict[str, str]:
    """'column = Dimension' lines → {dimension: column}. Blank lines and # comments are skipped."""
    out = {}
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"TM1 load: mapping line '{line}' should read: column = Dimension")
        col, dim = (x.strip() for x in line.split("=", 1))
        out[dim] = col
    return out


def column_for_dimensions(dims: list[str], columns: list[str], mapping: dict[str, str]) -> dict[str, str]:
    """{dimension: frame column}: from the mapping, else the column named like the dimension."""
    by_key = {_key(c): c for c in columns}
    mkey = {_key(d): c for d, c in mapping.items()}
    unknown = [d for d in mapping if _key(d) not in {_key(x) for x in dims}]
    if unknown:
        raise ValueError(f"TM1 load: the mapping names {', '.join(unknown)}, not dimensions of this cube "
                         f"({', '.join(dims)})")
    out, missing = {}, []
    for d in dims:
        col = mkey.get(_key(d)) or by_key.get(_key(d))
        if col is None or col not in columns:
            missing.append(d)
        else:
            out[d] = col
    if missing:
        raise ValueError(f"TM1 load: no column for dimension(s) {', '.join(missing)}. Columns: "
                         f"{', '.join(columns)}. Name the columns after the dimensions, or map them "
                         "(one 'column = Dimension' per line).")
    return out


def missing_elements(tm1, frame, dim_cols: dict[str, str]) -> dict[str, list[str]]:
    """Elements in the frame that the dimension doesn't have, per dimension."""
    out = {}
    for dim, col in dim_cols.items():
        have = {_key(e) for e in tm1.elements.get_element_names(dim, dim)}
        miss = sorted({str(v) for v in frame[col].dropna().unique() if _key(v) not in have})
        if miss:
            out[dim] = miss
    return out


def _member(dim: str, element: str) -> str:
    esc = lambda s: str(s).replace("]", "]]")  # noqa: E731
    return f"[{esc(dim)}].[{esc(dim)}].[{esc(element)}]"


def zero_out_slice(frame, dim_cols: dict[str, str]) -> dict[str, str]:
    """{dimension: MDX set} of the slice a frame covers: its distinct elements per dimension."""
    return {dim: "{" + ",".join(_member(dim, e) for e in sorted({str(v) for v in frame[col].unique()})) + "}"
            for dim, col in dim_cols.items()}


def slice_mdx(cube: str, sets: dict[str, str]) -> str:
    """The slice as one MDX query: every dimension's set on columns, non-empty cells only."""
    return f"SELECT NON EMPTY {' * '.join(sets.values())} ON 0 FROM [{cube.replace(']', ']]')}]"


def clear_slice(tm1, cube: str, dims: list[str], sets: dict[str, str]) -> str:
    """Zero the slice. ViewZeroOut (through TM1py) needs a data and ops admin;
    without those rights, read the slice's non-empty cells and write zeros,
    which needs only write access. Returns which way it went."""
    mdx = slice_mdx(cube, {d: sets[d] for d in dims})
    try:
        tm1.cells.clear_with_mdx(cube=cube, mdx=mdx)
        return "ViewZeroOut"
    except Exception as exc:                          # TM1pyNotAdminException, TM1pyVersionException, …
        if not any(w in type(exc).__name__ for w in ("Admin", "Version")):
            raise
    current = tm1.cells.execute_mdx_dataframe(mdx)
    if current is None or current.empty:
        return "nothing to zero"
    current = current[current["Value"].map(lambda v: isinstance(v, (int, float)))]
    if not current.empty:
        current = current.assign(Value=0)
        tm1.cells.write_dataframe(cube, current, dimensions=dims, increment=False, sum_numeric_duplicates=False)
    return "written zeros"


def _status(cube, mode, written, skipped, t0, *, dry_run=False, message=""):
    """One row. In a dry run, cells_written is what *would* be written."""
    import pandas as pd
    return pd.DataFrame([{
        "cube": cube, "mode": mode, "dry_run": dry_run, "cells_written": int(written),
        "cells_skipped": int(skipped), "duration_s": round(time.perf_counter() - t0, 3),
        "status": "dry run" if dry_run else "ok", "message": message,
    }])


def load(tm1, df, *, cube: str, mapping: str = "", value_column: str = "value", mode: str = "replace_slice",
         bulk: bool = False, unknown_elements: str = "fail", dry_run: bool = False):
    """Write `df` into `cube`; returns a one-row status frame."""
    import pandas as pd
    t0 = time.perf_counter()
    if mode not in MODES:
        raise ValueError(f"TM1 load: unknown mode '{mode}' ({', '.join(MODES)})")
    if not cube:
        raise ValueError("TM1 load: name the cube.")
    if df is None or not len(df.columns):
        raise ValueError("TM1 load: connect a node whose output holds the cells to write.")
    if value_column not in df.columns:
        raise ValueError(f"TM1 load: no value column '{value_column}'. Columns: {', '.join(map(str, df.columns))}")

    dims = list(tm1.cubes.get_dimension_names(cube))
    dim_cols = column_for_dimensions(dims, [str(c) for c in df.columns], parse_mapping(mapping))
    frame = pd.DataFrame({d: df[c].astype(str) for d, c in dim_cols.items()})
    frame["__value"] = df[value_column].values
    frame = frame[frame["__value"].notna()]

    missing = missing_elements(tm1, frame, dim_cols={d: d for d in dims})
    skipped = 0
    report = "; ".join(f"{d}: {', '.join(m[:10])}{f' (+{len(m) - 10} more)' if len(m) > 10 else ''}"
                       for d, m in missing.items())
    if missing:
        keep = pd.Series(True, index=frame.index)
        for d, m in missing.items():
            keep &= ~frame[d].map(_key).isin({_key(x) for x in m})
        if dry_run:
            return _status(cube, mode, int(keep.sum()), int((~keep).sum()), t0, dry_run=True,
                           message=f"Elements not in the cube's dimensions — {report}")
        if unknown_elements != "skip":
            raise ValueError(f"TM1 load: elements not in the cube's dimensions — {report}. "
                             "Add them, or set Unknown elements to Skip.")
        skipped = int((~keep).sum())
        frame = frame[keep]

    if dry_run:
        return _status(cube, mode, len(frame), skipped, t0, dry_run=True,
                       message=f"{len(frame):,} cells would be written; every element exists.")
    if frame.empty:
        return _status(cube, mode, 0, skipped, t0, message="Nothing to write.")

    if mode == "replace_slice":
        clear_slice(tm1, cube, dims, zero_out_slice(frame, {d: d for d in dims}))
    tm1.cells.write_dataframe(cube, frame.rename(columns={"__value": "Value"}), dimensions=dims,
                              increment=(mode == "add"), use_blob=bool(bulk), sum_numeric_duplicates=True)
    msg = f"Skipped rows with unknown elements — {report}" if skipped else ""
    return _status(cube, mode, len(frame), skipped, t0, message=msg)
