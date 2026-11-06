"""
Sidewinder Flow Studio — Report Viewer

Reads reports.duckdb (published by Report nodes) using SHORT-LIVED
read-only connections — open, query, close. Never holds the file open, so
the SFS backend (the single writer) is never blocked. (DuckDB allows many
read-only processes OR one writer — a held reader blocks the writer,
especially on Windows.)

Runs as the `reports` service: docker compose --profile reports up -d
"""
import os
import time

import duckdb
import pandas as pd
import streamlit as st

st.set_page_config(page_title="SFS Reports", page_icon="🐍", layout="wide")

DB_PATH = os.getenv("REPORTS_DB", "../backend/reports_data/reports.duckdb")


def _query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Open → query → close. Retries briefly if the writer has the file."""
    last_err: Exception | None = None
    for _ in range(6):
        try:
            con = duckdb.connect(DB_PATH, read_only=True)
            try:
                return con.execute(sql, params or []).df()
            finally:
                con.close()
        except Exception as e:  # writer holds the file, or file not created yet
            last_err = e
            time.sleep(0.4)
    raise last_err  # type: ignore[misc]


def load_meta() -> pd.DataFrame:
    try:
        return _query(
            "SELECT name, kind, code, row_count, updated_at FROM _reports ORDER BY updated_at DESC"
        )
    except Exception:
        return pd.DataFrame(columns=["name", "kind", "code", "row_count", "updated_at"])


# Cache the DATA (not a connection), keyed by table + freshness stamp so a
# re-published report invalidates automatically.
@st.cache_data(ttl=300, show_spinner=False)
def load_table(name: str, updated_at: float) -> pd.DataFrame:
    return _query(f'SELECT * FROM "{name}"')


meta = load_meta()

# ── Sidebar: report list ──────────────────────────────────────────────────────
st.sidebar.markdown("## 🐍 SFS Reports")

if meta.empty:
    st.sidebar.info("No reports published yet.")
    st.title("Sidewinder Flow Studio — Reports")
    st.markdown(
        "Add a **Report** node to a flow and run it. "
        "Published reports appear here automatically."
    )
    st.stop()

default = st.query_params.get("report")
names = meta["name"].tolist()
idx = names.index(default) if default in names else 0
selected = st.sidebar.radio("Reports", names, index=idx, format_func=lambda n: (
    f"{'🔍' if meta.set_index('name').loc[n, 'kind'] == 'explore' else '📊'} {n}"
))

row = meta.set_index("name").loc[selected]
st.sidebar.caption(
    f"{int(row['row_count']):,} rows · updated "
    f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(row['updated_at']))}"
)
if st.sidebar.button("↻ Refresh data"):
    st.cache_data.clear()
    st.rerun()

# ── Render ────────────────────────────────────────────────────────────────────
if row["kind"] == "explore":
    # Published by an Explore node before v1.22. Explore now runs inside SFS,
    # so only a preview is loaded here — these tables can be tens of millions of rows.
    st.markdown(f"### 🔍 {selected}")
    st.info("Explore has moved into Sidewinder Flow Studio. Re-run the flow and use "
            "**Open in Explore** on the Explore node. Below: the first 1,000 rows it last published.")
    st.dataframe(_query(f'SELECT * FROM "{selected}" LIMIT 1000'), use_container_width=True)
else:
    df = load_table(selected, float(row["updated_at"]))
    st.markdown(f"### 📊 {selected}")
    code = row["code"] or ""
    if not code.strip():
        st.dataframe(df, use_container_width=True)
    else:
        try:
            exec(code, {"st": st, "df": df, "pd": pd, "duckdb": duckdb})  # noqa: S102
        except Exception as e:
            st.error(f"Report code error: {e}")
            with st.expander("Report code"):
                st.code(code, language="python")
