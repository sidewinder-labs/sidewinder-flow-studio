# Sidewinder Flow Studio (SFS)

**A visual ETL tool where every node is a Python or SQL cell.** Build data
pipelines on a canvas, inspect the data at any point, and run the heavy work in
pandas, Polars, DuckDB — or push it down into your warehouse.

Think NiFi's canvas, Jupyter's cells, and a DAG — in one self-hosted tool.

> ⚠️ **Early release.** SFS is built and maintained by one person and is under
> active development. It works and is used for real pipelines, but expect rough
> edges. Issues and feedback are very welcome.

<!-- TODO: add a GIF here — drag nodes → run → inspect data → open dashboard -->

---

## Why SFS

Most pipeline tools make you choose: a visual tool that traps you in config
boxes, or code that gives you no visibility into what's happening between steps.

SFS gives you both. Each node holds real code. Between nodes, data is cached as
**Apache Arrow**, so you can click any node and see exactly what came out of it,
re-run from any point without recomputing everything upstream, and mix engines
freely in the same flow.

- **See your data mid-flow** — click any node to inspect its output
- **Re-run surgically** — "run from here" uses cached upstream frames
- **Mix engines per node** — pandas for the fiddly bits, DuckDB/Polars for the heavy lifting
- **Push down when it matters** — run SQL in the warehouse instead of moving rows
- **Bulk-load fast** — `fast_write` uses each database's native loader (1.2M rows to Postgres in ~10s)
- **Self-hosted** — your data never leaves your infrastructure

---


<img width="1280" height="720" alt="sfs-nyc-taxi-flow" src="https://github.com/user-attachments/assets/6d7c05de-757c-4d8d-a816-cc1be153eb6b" />


---
## Features

**Engines** (per node) — pandas · Polars (lazy/streaming) · DuckDB (SQL over Arrow) ·
SQL pushdown (SQLGlot dialect transpilation) · Ibis pushdown

**Connectors** (all free) — PostgreSQL, MySQL, SQLite, SQL Server, Oracle, Druid,
Snowflake, BigQuery, Redshift, ClickHouse, StarRocks, Doris, Trino, Databricks,
Azure Data Lake, Delta Lake, Apache Iceberg

**Nodes** — Trigger (manual/cron) · Processor · **Router** (conditional branching) ·
Stop/Tap · Table View · Chart · Profile (column stats) · Explore ·
Report (Streamlit) · Annotations · AI flow-design notes

**Workflow** — multi-input joins/unions · persist & resume checkpoints ·
shared functions · custom reusable processors · run history · scheduling

**AI-assisted** — generate node code from a description (using *your* API key or
a local Ollama model), or sketch a whole flow with AI notes and compile it

**Extensible** — add engines via a documented [plugin contract](docs/PLUGINS.md);
`/api/capabilities` surfaces them in the UI automatically. Engines can declare
form parameters, and can run in an **isolated virtual environment** so heavy
libraries (PyCaret, scikit-learn…) never disturb the ETL environment

---

## Quick start

**Desktop app (Windows, Linux):** just for you, on one computer? Download the
installer from [sidewinderlabs.dev/download](https://sidewinderlabs.dev/download/):
no Docker or Python needed. See [desktop/README.md](desktop/README.md) and the
[code signing policy](https://sidewinderlabs.dev/code-signing/).

**Docker (recommended for servers and teams):**

```bash
git clone https://github.com/sidewinder-labs/sidewinder-flow-studio.git
cd sidewinder-flow-studio
cp .env.docker .env
# generate a key and put it in .env as SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up --build -d
# UI → http://localhost:8080
```

**Local (development):**

```bash
# terminal 1 — backend
cd backend && pip install -e ".[all-connectors,pushdown]" && python main.py

# terminal 2 — frontend
cd frontend && npm install && npm run dev
# UI → http://localhost:5173
```

Full instructions, including the optional reports viewer and per-connector
driver extras: **[docs/INSTALLATION.md](docs/INSTALLATION.md)**

---

## Documentation

| Doc | What's in it |
|---|---|
| [INSTALLATION.md](docs/INSTALLATION.md) | Local, single-container, and multi-container setup |
| [CAPABILITIES.md](docs/CAPABILITIES.md) | Every feature with runnable examples |
| [PLUGINS.md](docs/PLUGINS.md) | Plugin contract for adding engines |
| [CURVE_FITTING.md](docs/CURVE_FITTING.md) | Fit models to measurements: parameters ± errors, fit plots, View as Python |
| [AGENTS.md](AGENTS.md) | Context file for AI coding agents |
| [EDITIONS.md](EDITIONS.md) | What is open core, and what the enterprise plugin will add |

---

## A 60-second example

```
[Trigger] → [Processor: pandas]  load CSVs from a folder
          → [Processor: DuckDB]  SELECT ... GROUP BY 1  (SQL over the frame)
          → [Profile]            null/distinct/min/max per column
          → [Processor: pandas]  fast_write(df, "warehouse", "public.sales")
          → [Report]             Streamlit dashboard
```

```python
# a processor node — pd, get_engine, fast_write etc. are pre-injected
df = read_files("/data/imports", "sales_*.csv", regex=r"\d{4}-\d{2}-\d{2}")
df = df[df["status"] == "complete"]
fast_write(df, "warehouse", "public.sales", mode="replace")
```

---

## Before you deploy it — please read

SFS runs the code you put in nodes. A few consequences are worth stating plainly
rather than leaving you to discover them.

**Node code is as privileged as a shell on the backend host.** Processor code is
executed with normal Python available: it can read the filesystem, make network
calls, read `.env`, and decrypt any stored connection credentials. Per-node
scopes isolate *variable names*, not permissions — it is not a security sandbox.
Authentication (planned) will not change this: any user allowed to edit a flow is
effectively trusted with the backend host. **Treat flow-edit access as equivalent
to shell access.** Run SFS for trusted developers, on isolated infrastructure,
with narrowly scoped database credentials — not as an open shared service.

**Data larger than RAM: engine-dependent.** DuckDB scans the cached Arrow file
from disk — projecting only the columns a query touches and spilling to disk
when a join or sort exceeds its memory limit — and Polars scans lazily and can
stream its result straight back out. Both handle frames larger than memory.
**pandas does not**: it loads the whole frame. Use Polars or DuckDB for large
data, and the SQL/Ibis pushdown engines when the data should never leave the
warehouse at all.

Note that node *boundaries* still materialise a full frame in the Arrow cache —
that is what makes inspect-and-rerun work. Streaming end-to-end and
inspectability are in tension; SFS chose inspectability, with out-of-core
processing inside each node.

**Execution is sequential by default**, with opt-in parallelism. Set
`SFS_PARALLEL_BRANCHES=1` to run independent branches concurrently on multiple
cores — useful whenever a flow fans out. There is no distributed execution
across machines; worker-queue scaling is on the roadmap.

**Scheduled retries re-run the whole flow.** A retry re-executes from the start,
not from the failed node. Make writes idempotent before enabling retries:
`fast_write(..., mode="replace")` is safe to repeat, `mode="append"` will
duplicate rows. Nodes that already succeeded before the failure will run again.

**Metadata defaults to SQLite** (single writer). Set `METADATA_DB_URL` to
PostgreSQL for anything beyond single-user use, e.g.
`postgresql+psycopg://user:pass@host:5432/flowstudio` in `.env`.

## Status & roadmap

Working today: everything in Features above, including the plugin system with
isolated runtimes.

Planned: auth/SSO and RBAC, worker-queue scaling, resume-from-failed-node, a
semantic catalog exposed over MCP, and the desktop app on macOS.

[EDITIONS.md](EDITIONS.md) maps every capability to its edition and implementation
status. Everything in this repository is open core, including the SQL and Ibis
pushdown engines and the advanced scheduler (retries, alerts, calendar rules).
Nothing here will be removed from it. Enterprise features — authentication and
SSO, RBAC, multi-machine scaling, support — are new capabilities that will ship as
a separately licensed plugin on top of this core.

---

## Contributing

Issues, bug reports and ideas are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Since this is a solo project, please open an issue before starting large work so
we can agree on direction.

## License

[AGPL-3.0](LICENSE). You can use, modify and self-host SFS freely. If you offer
a modified version as a network service, you must publish your changes.
