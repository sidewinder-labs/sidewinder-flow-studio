# Sidewinder Flow Studio (SFS) — Capabilities & Examples

A visual Python notebook-as-DAG for building, debugging, and scheduling data pipelines — NiFi's canvas meets Jupyter's cells. Every node is code; data flows between nodes as Apache Arrow; you can inspect any edge mid-flight.

This guide walks through every capability with runnable examples.

---

## Core concepts

**The canvas** is a directed graph of nodes. Data flows left to right along the edges. Each node runs, produces a frame, and passes it downstream.

**The Arrow contract.** Between every pair of nodes, data is stored as an Apache Arrow IPC file in the frame cache. This is what makes mixed-engine pipelines work: a pandas node, a Polars node, and a DuckDB node can sit in a chain and hand data to each other with near-zero conversion cost.

**Materialization & inspection.** Because each node's output is cached, you can click any node to inspect its data, and you can re-run from any point without recomputing everything upstream. This inspectability is the core design choice of SFS.

---

## Node types

| Node | Purpose |
|------|---------|
| **Trigger** | Entry point. Manual (click Run) or Schedule (cron). Holds scheduling config. |
| **Processor** | The workhorse. Runs code in one of five engines. Transforms `df`. |
| **Router** | Conditional branching — sends the flow down one of several paths based on expressions evaluated against the data. |
| **Stop / Tap** | Halts a branch (hard) or passes through while marking a checkpoint (tap). |
| **Profile** | Per-column statistics: nulls, distinct counts, top values, numeric stats, mini histogram. |
| **Table View** | Renders the incoming frame as a table. |
| **Chart View** | Renders a chart (ECharts / Plotly / matplotlib). |
| **Explore** | Keeps the frame from each run and opens it in the Explore view (drag-and-drop charts). |
| **Report** | Publishes an authored Streamlit dashboard to the report viewer. |

---

## The five engines

Each Processor node picks an engine. The engine only changes *how you write the transform* — the input and output are always Arrow frames, so engines mix freely in one flow.

### 1. pandas (default, free)

Full Python flexibility. The incoming frame is `df`; leave the result in `df`.

**Injected without imports:** `pd` (pandas), `sa` (sqlalchemy), `get_engine(name)`, `get_storage(name)`, `fast_read(...)`, `fast_write(...)`, `vars`.

```python
# Read from a connection, transform, done
df = fast_read("postgres_local", sql="SELECT * FROM employees.salary WHERE from_date >= '1990-01-01'")
df['year'] = pd.to_datetime(df['from_date']).dt.year
df = df.groupby('year', as_index=False)['amount'].mean()
```

### 2. Polars (free)

Lazy and streaming — the upstream frame is scanned as a `LazyFrame`, nothing loads until the plan collects (with the streaming engine, so larger-than-RAM plans can spill). `pl` is pre-injected. Stay lazy; don't call `.collect()` yourself.

```python
df = (df
      .filter(pl.col("from_date") >= pl.date(1990, 1, 1))
      .with_columns(pl.col("from_date").dt.year().alias("year"))
      .group_by("year")
      .agg(pl.col("amount").mean().alias("avg_salary")))
```

### 3. DuckDB (free)

Write SQL over the upstream frame, available as a table named `df` (additional inputs are `df2`, `df3`). DuckDB reads the cached Arrow file from disk rather than loading it into memory first, so it projects only the columns your query touches and spills to disk rather than failing on large joins.

**With no upstream node it is a source**: write any SQL DuckDB understands, including direct file reads — often faster than the pandas equivalent because filters push down into the reader.

```sql
SELECT * FROM read_csv_auto('/data/imports/sales_*.csv')
SELECT region, sum(amount) FROM read_parquet('/data/lake/**/*.parquet')
WHERE year = 2026 GROUP BY region
```

```sql
SELECT date_trunc('year', from_date) AS year,
       AVG(amount) AS avg_salary
FROM df
GROUP BY 1
ORDER BY 1
```

### 4. SQL pushdown

The SQL executes **in the target warehouse**, not on your machine. Pick a target connection in the node. SFS transpiles dialects with SQLGlot, so you can write in one flavor and target another. `SELECT`/`WITH` results become the node output; DDL/DML commands (`CREATE TABLE AS`, `COPY INTO`, `LOAD DATA`, `MERGE`) execute without fetching — perfect for warehouse-to-warehouse orchestration.

```sql
-- Runs inside the warehouse; only the result crosses into SFS
SELECT d.dept_name, AVG(s.amount) AS avg_salary
FROM employees.salary s
JOIN employees.department_employee de ON de.employee_id = s.employee_id
JOIN employees.department d ON d.id = de.department_id
WHERE s.to_date = DATE '9999-01-01' AND de.to_date = DATE '9999-01-01'
GROUP BY d.dept_name
```

### 5. Ibis

Pandas-like Python that compiles to warehouse SQL and executes there. Injected: `ibis`, `con` (the target backend), and `df` (upstream frame as a memtable, if any). Leave the result expression in `df`.

```python
t = con.table("salary", database="employees")
df = (t.filter(t.from_date >= ibis.date(1990, 1, 1))
        .group_by(year=t.from_date.year())
        .aggregate(avg_salary=t.amount.mean()))
```

### Engines from plugins

The five engines above are built in, but the engine list is extensible. A plugin
is an ordinary Python package that registers an engine through the
[plugin contract](PLUGINS.md); installing it makes the engine appear in the
picker with a 🧩 badge — no changes to SFS. Engines can also declare **form
fields** (a target column dropdown, a model selector, numbers, toggles) that
appear above the code editor.

Crucially, a plugin can run its work in **its own virtual environment**, with the
frame handed over as an Arrow file. The SFS process never imports the library, so
packages with aggressive version pins can be used without destabilising the ETL
environment. `plugins/sfs-plugin-sklearn` ships both an in-process and an
isolated variant of the same engine as a reference.

Plugins can also offer chart templates, example projects (**Start from an
example** in the project picker) and **View as Python** for their form-driven
engines. `plugins/sfs-plugin-curvefit`, included in the Docker images, uses all
of them: see [CURVE_FITTING.md](CURVE_FITTING.md).

### Memory behaviour

| Engine | Data larger than RAM? |
|---|---|
| pandas | **No** — loads the whole frame into memory |
| Polars | Yes — lazy scan of the cached file, streaming collect, can sink its result straight to disk |
| DuckDB | Yes — scans the cached file, reads only the columns the query needs, spills to disk above its memory limit |
| SQL / Ibis | N/A — the work happens in the warehouse; only results come back |

Set a DuckDB node's **Memory limit** (e.g. `4GB`) in the node panel, or
`SFS_DUCKDB_MEMORY_LIMIT` globally; `SFS_DUCKDB_TEMP_DIR` controls where it
spills. Below the limit DuckDB stays in memory, so small frames are unaffected.

### Parallel execution

By default nodes run one at a time in dependency order. Set
`SFS_PARALLEL_BRANCHES=1` to run independent branches concurrently — nodes are
grouped into layers where nothing in a layer depends on anything else in it, and
each layer runs together across cores. A flow that fans into three branches
processes all three at once.

**Which engine when?**

- Small/medium data, arbitrary Python, API calls → **pandas**
- Larger single-machine volumes, speed → **Polars** or **DuckDB**
- Data already in a warehouse; push the work down → **SQL** or **Ibis**

---

## Multi-input: joins and unions

A processor can have several upstream nodes. The first is `df`, the rest are `df2`, `df3`, … and `dfs` is the list of all of them. Run three branches in parallel, then merge.

**pandas — join:**
```python
df = df.merge(df2, left_on='employee_id', right_on='id', how='left')   # salary ⋈ employee
```

**pandas — union of all inputs:**
```python
df = pd.concat(dfs, ignore_index=True)
```

**DuckDB:**
```sql
SELECT * FROM df JOIN df2 ON df.employee_id = df2.id
-- or
SELECT * FROM df UNION ALL SELECT * FROM df2
```

**Polars:**
```python
df = df.join(df2, left_on="employee_id", right_on="id", how="left")   # or pl.concat(dfs)
```

---

## Branching, Stop & Tap, and surgical re-runs

A processor can fan out to multiple downstream branches, which run independently.

- **Stop (hard)** — halts its branch. Only *descendants* of the stop are blocked; sibling branches run normally. Halted edges show a "⊗ halted" badge.
- **Tap** — passes data through unchanged but marks a checkpoint you can inspect.

**Run from here** (right-click a processor) re-runs only that node and its descendants, using cached upstream frames. Nothing else recomputes.

**Release & run this branch** (right-click a hard Stop) flips it to tap and runs just that branch — the workflow for "I stopped this branch, inspected it, now let it through" without re-running the expensive upstream.

---

## Conditional branching — the Router node

A Router sends the flow down one of several paths depending on the data. Each
branch has a label and a Python expression evaluated against the incoming frame;
the first expression that is true wins, and the other branches are skipped for
that run.

```
[Load] → [Router]  ──empty──→  [Alert: no data arrived]
                   ──large──→  [Full reprocess]
                   ──else───→  [Normal incremental load]
```

Configure branches in the node panel. Each branch becomes a connection point on
the right edge of the node, so you wire them visually. Expressions see the frame
as `df` plus everything else injected into node code:

```python
len(df) == 0                      # nothing arrived
df.amount.sum() > 1_000_000       # threshold
df.status.eq('error').any()       # contains failures
vars['env'] == 'prod'             # a global variable
```

Branches that aren't taken are skipped, **but a node that several branches feed
into still runs** — so you can fan out and merge back. After a run, the node
shows which branch was taken.

## Reading data fast — `fast_read`

`pd.read_sql` turns every value into a Python object before pandas rebuilds the columns. `fast_read` reads straight into Arrow and returns the node's own type: a pandas DataFrame in a pandas node, a Polars DataFrame in a Polars node. The same line works in both.

```python
df = fast_read("postgres_local", table="employees.salary")
df = fast_read("postgres_local", tables=["sales_2025", "sales_2026"], schema="public")   # stacked, with _source_table
df = fast_read("postgres_local", sql="SELECT * FROM employees.salary WHERE amount > 60000",
               partition_on="employee_id", partitions=8)                                # 8 slices in parallel
```

- Give exactly one of `table`, `tables` or `sql`.
- `partition_on` (a numeric column) splits the read into `partitions` ranges read at the same time. Rows where the column is NULL are included.
- How it reads, shown under the node in the Output panel:
  - **ConnectorX** for PostgreSQL, Redshift, MySQL, SQL Server, Oracle and SQLite (`connectorx` comes with `all-connectors` and the desktop app). ConnectorX partitions on integer columns.
  - **ClickHouse Arrow**: ClickHouse's HTTP interface returns Arrow directly. ClickHouse already reads on all cores, so partitioning only helps on a column in the table's `ORDER BY`.
  - **SQLAlchemy** for anything else, for connections with extra connection options (e.g. Trino auth), or without `connectorx`. Partitioning is ignored there.
- `read_tables(conn, [tables])` still works and returns pandas; it is built on `fast_read`.

**The Database read node** does the same without code: a Processor with the **Database read** engine. Pick a connection, choose *A table*, *Several tables* or *A SQL query*, and optionally a partition column. Its output is Arrow, so any engine can follow, including DuckDB (which runs SQL over frames and can't read a database itself). **View as Python** shows the equivalent `fast_read` line.

Measured on this project's demo data (median of 3, in Docker):

| | `pd.read_sql` | `fast_read` | 4 partitions | 8 partitions |
|---|---|---|---|---|
| PostgreSQL, 2.84M rows | 4.9 s | 1.4 s | 0.65 s | **0.48 s** |
| ClickHouse, 2.96M rows (no sort key) | 88.5 s | **1.6 s** | 2.0 s | 3.4 s |

---

## Writing data fast — `fast_write`

To load a DataFrame into a database, use the injected `fast_write` helper rather than pandas' `df.to_sql`, which inserts row-by-row and is very slow for large volumes:

```python
fast_write(df, "postgres_local", "public.forex", mode="replace")   # append | replace
```

It accepts a pandas DataFrame, a Polars DataFrame **or LazyFrame**, or an Arrow
table — so it works in pandas and Polars nodes alike. **Missing tables are created
automatically** from the frame's schema (including the schema, e.g. `public`).
`mode="replace"` empties an existing table but keeps its definition; pass
`recreate=True` to drop and rebuild it when the columns have changed. For
PostgreSQL the data streams to `COPY` in chunks, so large frames don't need the
whole CSV in memory.

> `fast_write` is Python — use it in a **pandas or Polars** node. A DuckDB node
> runs SQL, so it can't call it; add a pandas or Polars node after the DuckDB one.

It automatically uses each database's native bulk loader — PostgreSQL `COPY`, ClickHouse native insert, SQL Server `fast_executemany`, MySQL/StarRocks/Doris batched insert, Oracle array binding, and Iceberg via pyiceberg — falling back to a batched insert for anything else. This is typically 10–100× faster than `to_sql`. For upserts, `fast_write` into a staging table then run your merge SQL via a SQL node.

## Persist & Resume (checkpointing)

Heavy first query you don't want to repeat tomorrow? Right-click a processor → **Persist output**. After each run its output is written to a local DuckDB file (or any configured database connection — the easy way to do `to_sql` without writing it).

Once persisted, the right-click menu offers:

- **Resume from persisted** — hydrates that node's frame from the saved table and runs only its descendants. The heavy node never executes. Works across restarts and days.
- **Flush persisted data** — drops the table so the next run starts fresh from source.

Configure the target (local DuckDB vs. a DB connection) and table name in the Processor panel's Persist section.

---

## Connections (all free)

Add connections in **Meta → Connections**. Passwords are encrypted at rest with your `SECRET_KEY`. Each has a **Test** button.

Supported dialects and what the fields mean:

| Dialect | Host | Database | Extra options (`k=v&k2=v2`) |
|---|---|---|---|
| PostgreSQL / MySQL / SQLite | host | db | — |
| SQL Server | server | db | `driver=ODBC Driver 18 for SQL Server` |
| Oracle | host | **service name** | — (thin mode, no client install) |
| Druid | broker host | `druid/v2/sql` | — |
| Snowflake | account id (`xy12345.eu-central-1`) | `DB/SCHEMA` | `warehouse=WH&role=ANALYST` |
| BigQuery | GCP project | dataset | `credentials_path=/path/key.json` |
| ClickHouse | host | database | `protocol=https` (for port 8443) |
| Redshift | cluster endpoint | db | — (port 5439) |
| Azure Data Lake | storage account | container | `sas_token=...` or `client_id/client_secret/tenant_id` |

**Using a connection in a processor:**
```python
eng = get_engine("Prod Postgres")
df = pd.read_sql("SELECT * FROM schema.table LIMIT 1000", eng)
```

**Azure Data Lake is storage, not SQL** — use `get_storage()`:
```python
opts = get_storage("Prod ADLS")
df = pd.read_parquet("abfs://container/path/data.parquet", storage_options=opts)
df.to_parquet("abfs://container/out/result.parquet", storage_options=opts)
```

---

## Variables

**Meta → Variables** holds reusable config values, available in any processor as `vars`:

```python
cutoff = vars["reporting_start_date"]
df = pd.read_sql(f"SELECT * FROM t WHERE d >= '{cutoff}'", get_engine("Prod Postgres"))
```

Treat variables as non-secret config, not a secrets store (use encrypted Connections for credentials).

---

## The AI agent

Every Processor, Chart, and Report node has a **Generate with AI** button. Describe what you want in plain language; the agent generates code using your **live upstream schema** (column names, dtypes, and a small sample) for context, so it knows your actual columns. It's engine-aware — a DuckDB node gets SQL, a Polars node gets lazy Polars, an Ibis node gets Ibis, a Report node gets Streamlit.

Flow is review-before-insert: it shows the code, notes whether it had your schema, and you accept or retry. Nothing runs automatically.

### AI providers (Meta → AI)

Configure providers in the UI (keys encrypted at rest). Two-tier setup works nicely:

- **Local** — Ollama with e.g. `qwen2.5-coder:7b`. No key, runs offline. Base URL: `http://localhost:11434` when the backend runs directly on your machine; `http://host.docker.internal:11434` when it runs in Docker. In the Docker case Ollama must also listen beyond loopback — it binds `127.0.0.1` by default, which containers can't reach — so start it with `OLLAMA_HOST=0.0.0.0:11434 ollama serve` (or set that variable in its systemd unit), and keep port 11434 closed to your network in the firewall, since Ollama has no authentication.
- **Premium** — Anthropic Claude with an API key, or any OpenAI-compatible endpoint.

Toggle the active provider with one click; the AgentBox shows which provider generated each snippet. Also supports a legacy `ANTHROPIC_API_KEY` in `.env` as a fallback.

---

## Reports & Explore

**Explore node** — no code. Connect it, run the flow, then click **Open in Explore** on the node (or in its panel). It opens the node's output from the last run in the Explore view below. Before v1.22 this node published a PyGWalker explorer to the report viewer; reports published that way still show in the viewer as a plain table.

**Explore view** — the **Explore** button in the top bar opens a drag-and-drop explorer (Graphic Walker) inside SFS, over any node output from a flow run or any persisted table. Charts are compiled to SQL and run by DuckDB on the server, so a 30M-row frame answers in well under a second and only the aggregated result reaches the browser. **Show SQL** at the bottom lists every query with its timings. Guardrails: a view returns at most `EXPLORE_ROW_CAP` rows (default 50,000; a banner says when it was capped) and a query is stopped after `EXPLORE_TIMEOUT_S` seconds (default 30). A view can be bookmarked — the URL carries the source (`#/explore?kind=frame&project_id=…&node_id=…`).

**Exploring a database directly** — under **Databases — live** in Explore, open any connection to browse its schemas and tables (with row estimates where the database keeps them), or choose **SQL query…** to explore the result of your own SELECT, written in that database's SQL. No flow is needed. Charts are compiled to the database's own dialect and run there, so only aggregates travel: on ClickHouse a chart over 3M rows typically answers in 10–200 ms. Guardrails for production databases:

- the same row cap, plus a statement timeout enforced by the database itself where it supports one (PostgreSQL, Redshift, MySQL, ClickHouse, Snowflake, Trino); elsewhere SFS stops waiting after `EXPLORE_TIMEOUT_S`
- a SQL source must be a single SELECT — anything else is refused before it reaches the database
- date filters are sent as plain ranges on the column, so indexes and partition pruning still apply
- **use read-only credentials for exploration connections.** A ClickHouse user with `readonly = 1` cannot change any setting, so its timeout falls back to SFS's own; `readonly = 2` keeps the user read-only and lets the server enforce it

Median is exact on PostgreSQL, ClickHouse and DuckDB, approximate on Trino, and not offered on MySQL or SQL Server, which have no median aggregate. Dates are bucketed as stored (no time-zone shift); change *Display offset* in a chart's config if your timestamps are in UTC and you want local days.

**Explore → flow** — **Create node** (top right of the chart) adds what you built to the flow you have open, as an unconnected processor, saved:

- *The data, with this chart's filters* (default) — every column of the source, filtered like the chart; the flow does its own shaping. Filters on computed fields (bins, date parts) belong to the chart and are left out, with a note
- *This chart's result* — the aggregated rows the chart shows

From a node output or persisted table you get a **DuckDB** processor over `df` — connect the source node to it. From a database you choose the engine: **pandas** runs the query through `get_engine()` and brings the result into the flow; **SQL ⚡** runs it inside the database and returns only the result.

**Drill-through** — on a node output or persisted table, **Drill** fetches the detail rows behind a chart point from a live table. Set it up once per source: pick the connection and detail table, and map the fields that identify a row (same-named text and date columns are suggested). The panel lists the chart's points; picking one runs `SELECT * FROM <detail> WHERE …` with that point's values and the chart's filters on the mapped columns, under the same guardrails, showing up to 1,000 rows. Fields without a mapping are listed as not applied — nothing is guessed. **Explore these rows** opens the detail query as a live SQL source.

**Report node** — a data product viewable by people who never open SFS: the optional Streamlit viewer reads a shared DuckDB file. Authored dashboard. Write Streamlit code (or have the AI draft it). Pre-injected: `st`, `df`, `pd`.

```python
c1, c2, c3 = st.columns(3)
c1.metric("Rows", len(df))
c2.metric("Avg salary", f"{df['amount'].mean():,.0f}")
c3.metric("Departments", df['dept_name'].nunique())

st.bar_chart(df.groupby('dept_name')['amount'].mean())
st.dataframe(df)
```

See INSTALLATION.md for starting the viewer (a third terminal locally, or `--profile reports` in Docker).

---

## Charts inline

The **Chart View** node renders inside the canvas. Set `result` to an ECharts option dict (most reliable), a Plotly figure, or draw with matplotlib.

```python
# ECharts
df_m = df.groupby('dept_name')['amount'].mean()
result = {
    "xAxis": {"data": df_m.index.tolist()},
    "yAxis": {},
    "series": [{"type": "bar", "data": df_m.values.tolist()}]
}
```

```python
# Plotly
import plotly.express as px
result = px.line(df, x="year", y="avg_salary", color="dept_name")
```

---

## Scheduling

On the **Trigger** node, switch Run Mode to **Schedule** and set a cron expression + timezone.

```
0 6 * * 1-5     # weekdays at 06:00
0 */4 * * *     # every 4 hours
30 2 1 * *      # 02:30 on the 1st of each month
```

**Advanced scheduling**, on the same trigger node:

- **Retries** with **exponential backoff** (`wait × 2^(attempt−1)`)
- **Failure alerting** — Slack webhook + email (configure `SLACK_WEBHOOK_URL` / `SMTP_*` in `.env`)
- **Calendar rules** — weekdays-only, and a skip-dates list for holidays
- **Run history / audit** — every run and retry recorded, visible in **Meta → Runs** (status, trigger, attempt, duration, node counts, errors)

A schedule runs only while its flow is **active** (the dot on the flow tab). Schedules are loaded when the backend starts, so they survive restarts.

---

## Deleting projects and flows

- **Delete a project** from the project picker (hover a project, click the bin) and type its name to confirm. It moves to the **Trash** (bottom of the picker), where it can be restored or deleted permanently. Restored flows come back **turned off**, so no schedule starts firing until you turn it on again.
- **Delete a flow** with the × on its tab. The project is saved straight away.
- Either way, the deleted flows' schedules stop and their cached node outputs are removed (running again recreates them). Run history is kept.
- **Persisted tables** (local DuckDB) are kept unless you tick *Also delete persisted tables*. Only tables no other flow persists to are offered; tables persisted to a database connection are never touched.
- Deleting is refused while a flow that would be deleted is running.

---

## Worked example: warehouse-to-warehouse transfer

Move salary data from Snowflake to BigQuery, orchestrated by SFS — the data never touches your machine.

```
[Trigger: schedule 0 5 * * *]
        │
[Processor · SQL engine · target = Snowflake]
   COPY INTO @gcs_stage/salary
   FROM (SELECT employee_id, amount, from_date, to_date
         FROM employees.salary
         WHERE from_date >= DATEADD(day, -1, CURRENT_DATE))
   FILE_FORMAT = (TYPE = PARQUET);
        │
[Processor · SQL engine · target = BigQuery]
   LOAD DATA INTO analytics.salary_daily
   FROM FILES (format='PARQUET', uris=['gs://my-stage/salary/*']);
        │
[Processor · SQL engine · target = BigQuery]   -- validation
   SELECT COUNT(*) AS rows_loaded FROM analytics.salary_daily;
        │
[Table View]   -- the small validation result is fine to pull into pandas
```

Both bulk steps run inside the warehouses; only the final count crosses into SFS.

---

## Worked example: daily P&L dashboard

```
[Trigger: schedule 0 6 * * 1-5, retries=2, alert_on_failure=on, weekdays_only=on]
        │
[Processor · pandas]   read raw P&L from Postgres, cast dates
        │
[Processor · DuckDB]   SELECT ... aggregate by cost center & month
        │
[Persist: ON → local]  checkpoint the aggregate
        │
        ├─[Report node]   authored Streamlit KPI dashboard
        └─[Explore node]  ad-hoc slicing in the Explore view
```

Runs every weekday at 06:00, retries twice with backoff on failure, alerts on final failure, publishes a curated dashboard, and keeps the aggregate ready for ad-hoc slicing in Explore.

---

## Tips

- **Inspect liberally.** Click any node after a run to see its data. Tap edges you're unsure about.
- **Persist heavy nodes** early in development so you're not re-running expensive source queries on every iteration.
- **Keep engines per-node.** Use pandas for the fiddly Python bits and SQL/DuckDB/Polars for the heavy relational work; the Arrow boundaries between them are cheap.
- **Let the agent draft, then read it.** Especially for charts and Streamlit reports where the boilerplate is tedious.
- **Push down at scale.** The further you push filtering/aggregation toward the source (SQL/Ibis engines, or SQL in a pandas node), the less data crosses into SFS.
