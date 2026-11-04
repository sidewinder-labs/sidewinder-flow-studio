"""
Agent service — generates processor and chart code using the Anthropic API.

The agent receives:
  - The user's natural-language request
  - The upstream DataFrame schema (column names + dtypes) if available
  - A sample of the data (first few rows) for context
  - The target node type (processor or chart)

It returns generated Python code ready to paste into the node.
"""
from __future__ import annotations

import os
import json
from typing import Any

import httpx

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
# Model used for code generation
AGENT_MODEL = "claude-sonnet-4-6"


def with_helpers(prompt: str) -> str:
    """Fill {HELPERS} with the injected helpers, from the same reference as the
    in-app help (so plugin helpers are included and nothing is written twice)."""
    from app.help.reference import helper_reference
    lines = [f"  - `{h['signature']}`: {h['summary']}"
             for h in helper_reference() if h["sfs_specific"]]
    lines.append("  - `pd` (pandas), `sa` (sqlalchemy). Read databases with fast_read and write with "
                 "fast_write: far faster than pd.read_sql and df.to_sql.")
    return prompt.replace("{HELPERS}", "\n".join(lines))


REPORT_SYSTEM_PROMPT = """You are a dashboard assistant inside Sidewinder Flow Studio (SFS), a visual ETL tool.

You write Streamlit code for a "Report" node. The code runs inside an existing Streamlit page. Rules:
- Pre-injected, available WITHOUT imports: `st` (streamlit), `df` (the incoming pandas DataFrame), `pd` (pandas)
- Do NOT call st.set_page_config (the host page already did)
- Build a compact dashboard: st.metric row for KPIs, st.bar_chart / st.line_chart / st.area_chart for trends, st.dataframe for detail
- Convert date columns with pd.to_datetime() before time-based grouping
- st.columns() for KPI rows; st.selectbox/st.multiselect for simple filters that subset df
- Do NOT include markdown code fences or explanations
- Output ONLY the Python code, nothing else"""


SQL_PUSHDOWN_SYSTEM_PROMPT = """You are a data engineering assistant inside Sidewinder Flow Studio (SFS), a visual ETL tool.

You write warehouse SQL for a "Processor" node in SQL pushdown mode. Rules:
- The SQL executes IN the target warehouse (Snowflake, BigQuery, Postgres, Redshift, ClickHouse, …) — SFS transpiles dialects with SQLGlot, so write standard SQL unless the task needs vendor commands
- SELECT / WITH results are fetched as the node's output frame; commands (CREATE TABLE AS, COPY INTO, LOAD DATA, MERGE) execute without fetching — ideal for warehouse-to-warehouse orchestration
- Reference warehouse tables by their real schema-qualified names; the upstream SFS frame is NOT available here
- Multiple statements separated by ; run in order
- Do NOT include markdown code fences or explanations
- Output ONLY the SQL, nothing else"""


IBIS_SYSTEM_PROMPT = """You are a data engineering assistant inside Sidewinder Flow Studio (SFS), a visual ETL tool.

You write Ibis code for a "Processor" node in Ibis pushdown mode — pandas-like Python that compiles to SQL and executes in the target warehouse. Rules:
- Pre-injected: `ibis`, `con` (the warehouse backend), and `df` (the upstream SFS frame as an ibis memtable, when present)
- Get warehouse tables with con.table("schema.name") or con.table("name", database="schema")
- Chain expressions: .filter(), .group_by().agg(), .mutate(), .join(), .order_by() — stay as expressions, no .to_pandas()/.execute(); SFS executes the final `df` in the warehouse
- Leave the result expression in `df`
- Do NOT include markdown code fences or explanations
- Output ONLY the Python code, nothing else"""


POLARS_SYSTEM_PROMPT = """You are a data transformation assistant inside Sidewinder Flow Studio (SFS), a visual ETL tool.

You write Polars code for a "Processor" node running in Polars engine mode. Rules:
- The incoming data is a polars LazyFrame called `df` (additional inputs: `df2`, `df3`, …; `dfs` is the list)
- `pl` (polars) is pre-injected; also (fast_read returns a Polars DataFrame here):
{HELPERS}
- Stay LAZY: chain expressions on the LazyFrame (filter, with_columns, group_by/agg, join) — do NOT call collect(); the engine collects with streaming at the end
- Leave the result in `df` (LazyFrame preferred; a pl.DataFrame is also accepted)
- Use expression API: pl.col("x"), .str, .dt namespaces; cast dates with pl.col("d").str.to_date() or .cast(pl.Date)
- Do NOT include markdown code fences or explanations
- Output ONLY the Python code, nothing else"""


DUCKDB_SYSTEM_PROMPT = """You are a data transformation assistant inside Sidewinder Flow Studio, a visual ETL tool.

You write DuckDB SQL for a "Processor" node running in DuckDB engine mode. Rules:
- The incoming data is available as a table named `df`
- If the node has multiple upstream inputs, they are tables `df2`, `df3`, … (join/union them in SQL)
- Write a single DuckDB SQL statement (SELECT / WITH ... SELECT) whose result becomes the node's output
- Use DuckDB SQL syntax and functions (date_trunc, strftime, list/struct functions, QUALIFY, etc. are available)
- DuckDB is case-insensitive for identifiers unless quoted
- Add brief SQL comments (-- ...) explaining non-obvious steps
- Do NOT include markdown code fences, explanations, or any text outside the SQL
- Output ONLY the SQL, nothing else"""


FLOW_SOURCE_SYSTEM_PROMPT = """You are compiling one node of a data flow in Sidewinder Flow Studio (SFS).

Write pandas code for a SOURCE processor that loads data. Rules:
- Available (no imports needed): pd, sa, get_engine(name), get_storage(name), read_files(folder, pattern, regex=...), vars
- The user gives you a structured spec (connection, and either full SQL, OR table/columns/filter/group_by, OR a file path/pattern)
- If full SQL is given: use it verbatim — df = pd.read_sql(sa.text(<the sql>), get_engine(<connection>))
- If table fields are given: assemble a single-table SELECT from table/columns/filter/group_by — do NOT invent joins
- If a file path is given: use pd.read_csv/read_parquet/read_excel by extension, or read_files() for patterns
- Leave the result in `df`
- End your response with a comment line listing the exact output columns: # OUTPUT_COLUMNS: col1, col2, ...
- Output ONLY Python code, no markdown fences, no explanations"""


FLOW_STEP_SYSTEM_PROMPT = """You are compiling one node of a data flow in Sidewinder Flow Studio (SFS).

Write pandas code for a TRANSFORM processor. Rules:
- The incoming DataFrame is `df` (additional inputs df2, df3 if the spec mentions them); pd, sa, vars are available
- You are told the upstream columns — use ONLY those columns as inputs; do not invent columns
- Apply the user's described transformation and leave the result in `df`
- Column types are unknown at this point (dates may be strings or Python date objects), so convert BOTH sides before comparing dates:
  df['to_date'] = pd.to_datetime(df['to_date'])
  df = df[df['to_date'] == pd.Timestamp('9999-01-01')]
- If the user declared expected output columns, make your code produce exactly those names
- End your response with a comment line listing the exact output columns: # OUTPUT_COLUMNS: col1, col2, ...
- Output ONLY Python code, no markdown fences, no explanations"""


FLOW_SINK_SYSTEM_PROMPT = """You are compiling one node of a data flow in Sidewinder Flow Studio (SFS).

Write pandas code for a SINK processor that writes `df` to a destination. Rules:
- Available: pd, sa, get_engine(name), vars; the incoming DataFrame is `df`
- Database target: use fast_write(df, <connection>, <table>, mode=<'replace'|'append'>) — native bulk load (COPY/native insert/etc), NOT df.to_sql which is row-by-row and slow
- If the user provided custom/upsert SQL: first write df to the temp table they name (if_exists='replace'), then execute their SQL statements verbatim via engine.begin()
- File target: df.to_csv / to_excel / to_parquet by extension
- Keep `df` unchanged so it can still flow to viewers
- End with: # OUTPUT_COLUMNS: (same as input)
- Output ONLY Python code, no markdown fences, no explanations"""


PROCESSOR_SYSTEM_PROMPT = """You are a data transformation assistant inside Sidewinder Flow Studio (SFS), a visual Python ETL tool.

You write Python code for a "Processor" node. Rules:
- The incoming data is in a pandas DataFrame called `df`
- If the node has multiple upstream inputs, the 2nd is `df2`, 3rd is `df3`, … and `dfs` is the list of all inputs (use for joins/unions)
- Pre-injected, no imports needed:
{HELPERS}
- Your code MUST leave the result in a DataFrame called `df`
- Write clean, production-quality pandas code
- Add brief comments explaining each step
- Do NOT include markdown code fences, explanations, or any text outside the code
- Output ONLY the Python code, nothing else
- If you need additional imports (numpy, datetime, etc.), include them at the top
- Handle data types carefully — dates from SQL often arrive as strings or Python date objects (dtype object); convert with pd.to_datetime() before comparing"""


CHART_SYSTEM_PROMPT = """You are a data visualisation assistant inside Sidewinder Flow Studio (SFS), a visual Python ETL tool.

You write Python code for a "Chart View" node. Rules:
- The incoming data is in a pandas DataFrame called `df`
- `pd` (pandas) is available without import
- You MUST set a variable called `result` to ONE of these:
  1. An Apache ECharts option dict (preferred for most charts) — e.g. result = {"xAxis": {...}, "yAxis": {...}, "series": [...]}
  2. A Plotly figure — import plotly.express as px; result = px.line(...)
  3. For matplotlib/seaborn, draw the figure and do NOT set result (it's auto-captured)
- Prefer ECharts dicts for bar/line/pie charts — they render most reliably
- For ECharts, convert pandas Series to lists with .tolist(), and dates to strings with .astype(str) or .dt.strftime("%Y-%m-%d")
- Handle data types carefully — convert date columns with pd.to_datetime() before using .dt accessor
- Do NOT include markdown code fences or explanations
- Output ONLY the Python code, nothing else"""


def _build_context_message(request: str, schema: list[dict] | None, sample: list[dict] | None) -> str:
    parts = [f"Task: {request}\n"]

    if schema:
        parts.append("\nThe `df` DataFrame has these columns:")
        for col in schema:
            parts.append(f"  - {col['name']} ({col.get('dtype', 'unknown')})")

    if sample:
        parts.append(f"\nSample of the first {len(sample)} rows:")
        parts.append(json.dumps(sample[:3], indent=2, default=str))

    if not schema and not sample:
        parts.append("\n(No upstream schema available — write general code based on the task description.)")

    parts.append("\n\nWrite the Python code now. Output only code, no markdown fences.")
    return "\n".join(parts)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if the model included them despite instructions."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop first line (```python or ```)
        lines = lines[1:]
        # Drop trailing ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


async def generate_code(
    request: str,
    node_type: str,                      # "processor" or "chart"
    schema: list[dict] | None = None,
    sample: list[dict] | None = None,
    db: Any = None,                      # AsyncSession — used to look up active provider
    engine: str = "pandas",              # "pandas" | "duckdb" — selects the prompt
    system_override: str | None = None,  # custom system prompt (flow compilation)
) -> dict[str, Any]:
    """
    Call the active AI provider to generate code. Returns {code, error, provider}.

    Resolution order:
      1. Active provider configured in the UI (agent_providers table)
      2. ANTHROPIC_API_KEY from .env / settings (legacy fallback)
      3. Otherwise, a helpful error
    """
    if system_override:
        system = system_override
    elif node_type == "chart":
        system = CHART_SYSTEM_PROMPT
    elif node_type == "report":
        system = REPORT_SYSTEM_PROMPT
    elif engine == "duckdb":
        system = DUCKDB_SYSTEM_PROMPT
    elif engine == "polars":
        system = with_helpers(POLARS_SYSTEM_PROMPT)
    elif engine == "sql":
        system = SQL_PUSHDOWN_SYSTEM_PROMPT
    elif engine == "ibis":
        system = IBIS_SYSTEM_PROMPT
    else:
        system = with_helpers(PROCESSOR_SYSTEM_PROMPT)
    user_message = _build_context_message(request, schema, sample)

    # ── 1. Try the active UI-configured provider ──────────────────────────────
    if db is not None:
        try:
            from app.services.agent_provider_service import get_active_provider, complete
            provider = await get_active_provider(db)
            if provider:
                try:
                    text = await complete(provider, system, user_message)
                    code = _strip_code_fences(text)
                    if not code:
                        return {"code": None, "error": "Agent returned no code.", "provider": provider.name}
                    return {"code": code, "error": None, "provider": provider.name}
                except httpx.HTTPStatusError as e:
                    return {"code": None, "error": f"{provider.name}: HTTP {e.response.status_code}: {e.response.text[:200]}", "provider": provider.name}
                except Exception as e:
                    from app.services.agent_provider_service import explain_error
                    return {"code": None, "error": f"{provider.name}: {explain_error(provider, e)}", "provider": provider.name}
        except Exception:
            pass  # fall through to env fallback

    # ── 2. Legacy .env fallback (Anthropic only) ──────────────────────────────
    try:
        from app.core.config import settings
        api_key = settings.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY")
    except Exception:
        api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        return {
            "code": None,
            "error": "No AI provider configured. Add one in the AI Providers panel, "
                     "or set ANTHROPIC_API_KEY in your backend .env file.",
            "provider": None,
        }

    payload = {
        "model": AGENT_MODEL,
        "max_tokens": 4000,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(ANTHROPIC_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        text_parts = [
            block["text"] for block in data.get("content", []) if block.get("type") == "text"
        ]
        code = _strip_code_fences("\n".join(text_parts))
        if not code:
            return {"code": None, "error": "Agent returned no code.", "provider": "env:anthropic"}
        return {"code": code, "error": None, "provider": "env:anthropic"}
    except httpx.HTTPStatusError as e:
        return {"code": None, "error": f"API error {e.response.status_code}: {e.response.text[:200]}", "provider": "env:anthropic"}
    except Exception as e:
        return {"code": None, "error": f"Agent request failed: {str(e)}", "provider": "env:anthropic"}
