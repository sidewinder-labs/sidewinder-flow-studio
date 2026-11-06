# SFS Editions — Open Core vs Enterprise

This document maps every SFS capability to its **edition**: open core (this
repository, AGPL-3.0) or enterprise (the private `sfs-enterprise` plugin,
commercially licensed).

Status legend:
- ✅ **Implemented** — built and working
- 🟡 **Partial** — works but incomplete or not production-hardened
- ⬜ **Planned** — designed/discussed, not yet built

> **How the editions fit.** Open core is the complete product. Enterprise is a
> plugin package installed on top of it through the `sfs.plugins` entry point
> (docs/PLUGINS.md) — one codebase, no fork. Enterprise holds **new**
> capabilities only; nothing published in this repository is moved out of it.
> Where an enterprise feature needs a hook in core, the hook is open (with a
> no-op default) and the implementation lives in the plugin.

---

## Open-core capabilities (free, AGPL-3.0)

These are the complete, genuinely useful free product. Nothing here is crippled.

### Canvas & flow authoring
- ✅ Visual DAG canvas (React Flow) — drag nodes, wire edges
- ✅ Per-node code cells (Monaco editor)
- ✅ Multi-flow projects
- ✅ Node inspection — click any node to view its cached data
- ✅ Edge labels showing variable name (`df`/`df2`) + row count
- ✅ Input-variable chips in the processor panel
- ✅ Annotation notes (colored, resizable, render behind, never execute)
- ✅ Run-from-here / resume / branch execution controls
- ✅ **Router node** — conditional branching; expressions evaluated against `df`,
  one source handle per branch, untaken branches skipped

### Execution engines (per-processor choice)
- ✅ **pandas** — full Python
- ✅ **Polars** — lazy, streaming, Arrow-native
- ✅ **DuckDB** — SQL over the cached Arrow file: column projection, spills to
  disk above its memory limit, and usable as a source node (read_csv/read_parquet)
- ✅ Arrow IPC contract between all nodes (mixed engines interoperate)
- ✅ **Out-of-core processing on one machine** — Polars lazy scan + streaming
  sink, DuckDB disk scan + spilling. Larger-than-RAM data is a free capability,
  not a paid one (pandas remains in-memory by nature)
- ✅ **Parallel branch execution** — independent branches run concurrently on
  multiple cores (`SFS_PARALLEL_BRANCHES=1`)
- ✅ **SQL pushdown** — SQL runs inside the target warehouse, dialects
  transpiled by SQLGlot (`pip install ".[pushdown]"`)
- ✅ **Ibis pushdown** — pandas-like Python compiled to warehouse SQL

### Data movement
- ✅ Multi-input joins/unions (`df`, `df2`, `df3`, `dfs`)
- ✅ `read_files` / `list_files` — multi-file loading with glob + regex
- ✅ `read_tables` — multi-table loading
- ✅ `read_delta` / `read_iceberg` — direct table-format reads
- ✅ **`fast_write`** — native bulk load (Postgres COPY, ClickHouse native,
  SQL Server fast_executemany, MySQL/StarRocks/Doris batched, Oracle array
  bind, Iceberg via pyiceberg; batched to_sql fallback)
- ✅ Persist & Resume (checkpoint node output to DuckDB or a DB connection)

### Connectors (all free — breadth is not gated)
- ✅ PostgreSQL, MySQL, SQLite, SQL Server, Oracle, Druid (core/opt drivers)
- ✅ Snowflake, BigQuery, Redshift, ClickHouse
- ✅ StarRocks, Doris, Trino, Databricks
- ✅ Azure Data Lake (storage, via `get_storage`)
- ✅ Delta & Iceberg (table formats — via engine or direct helpers)
- ✅ Connection management: create / edit / delete / test, Fernet-encrypted
  credentials, dialect-aware URL building, Trino auth/verify connect_args

### Outputs & inspection
- ✅ Table View node
- ✅ Chart View node (ECharts / Plotly / matplotlib)
- ✅ Profile node (per-column stats: nulls, distinct, top values, numeric
  stats, text histogram)
- ✅ **Explore view** (Graphic Walker) — drag-and-drop charts compiled to SQL and
  run where the data lives, over cached node outputs, persisted tables, and
  **live connections** (browse tables or explore a SELECT, before any flow
  exists); row cap, timeouts and read-only SQL guard included
- ✅ Explore node (no code) — opens its output from the last run in the
  Explore view
- ✅ Report node (Streamlit dashboards)
- ✅ Streamlit reports viewer app

### Extensibility
- ✅ **Engine registry + plugin system** — add engines via `sfs.plugins` entry
  points, no core changes (docs/PLUGINS.md)
- ✅ `/api/capabilities` — engines/plugins/features discovery; UI renders from it
- ✅ **Declarative engine parameters** — engines declare form fields (string,
  number, boolean, select, column, connection) rendered above the code editor
- ✅ **Isolated plugin runtime** — run a plugin in its own virtual environment,
  exchanging Arrow files, so heavy/conflicting dependencies never enter the core
  environment (`app/engine/runtime.py`); reference implementation in
  `plugins/sfs-plugin-sklearn`

### Productivity
- ✅ Shared functions / imports preamble (flow-level)
- ✅ Custom processors (save a processor, reuse via toolbar drag)
- ✅ Variables (global config dict)
- ✅ Function library — reusable functions copied into flows, with provenance and deliberate updates
- ✅ Export / import of flows and projects (`.sfs.json`: new ids, connection mapping,
  schedules inactive) and of connection metadata (no secrets; imported as *needs credentials*)
- ✅ Deterministic project files (byte-identical on an unchanged save)
- ✅ Explore table browser — schemas first, lazy loading, in-database search,
  PostgreSQL partitions hidden behind their parent
- ✅ Built-in AI agent — code generation per engine/node type (uses user's own
  provider: Anthropic / Ollama / OpenAI-compatible)
- ✅ AI Flow Builder — design with AI Start/Step/End notes, "Build with AI"
  compiles them into processors (topological, schema-threaded)
- ✅ Cron scheduling (trigger node, cron + timezone)
- ✅ Advanced scheduling — retries with exponential backoff, failure alerts
  (Slack + email), calendar rules (weekdays only, skip dates)
- ✅ `AGENTS.md` context file for external AI agents

### Ops & deployment
- ✅ Desktop app (Windows, Linux) and Docker (single / multi-container)
- ✅ Self-healing data directories, SECRET_KEY encryption
- ✅ Run history / audit table + Runs panel

---

## Enterprise capabilities

Enterprise lives in the private `sfs-enterprise` repository as one plugin
package. The package exists and loads; no enterprise feature is built yet.

> **Commitment:** functionality already published in the open repository will not
> be retroactively removed from it. The SQL and Ibis pushdown engines and the
> advanced scheduler were once marked enterprise; they are open core and stay
> that way.

### Roadmap

Rows marked *Core (open)* are open-core work that enterprise builds on.

| Capability | Status | Edition | Notes |
|---|---|---|---|
| **Engine-registry + plugin loader** (entry-points; `/api/capabilities`) | ✅ | Core (open) | DONE — `app/engine/registry.py`, contract in docs/PLUGINS.md. |
| **Licensing** — Ed25519 offline-signed keys, seat/expiry/features | ⬜ | Enterprise | Verifies offline; perpetual = `expires:null`. |
| **Auth — local** (argon2 + JWT), login, WS token validation | ⬜ | Enterprise | Auth interface with a no-op default in open core; implementation in the plugin. |
| **Auth — SSO/OIDC** (Azure AD etc.) | ⬜ | Enterprise | The #1 enterprise procurement ask. |
| **RBAC** (viewer/editor/admin; viewers see reports only) | ⬜ | Enterprise | Pairs with reports. |
| **Isolated plugin runtime** — separate venv per plugin, Arrow file handoff | ✅ | Core (open) | DONE — `app/engine/runtime.py`, exposed to plugins as `api.run_isolated()`. |
| **Worker queue scaling** — Celery/RQ workers, Postgres metadata, shared frame store | ⬜ | Enterprise (plugin + infra) | Distributed execution across machines and concurrent users — distinct from the free single-machine parallelism. |
| **Vector/MCP semantic catalog** — profile tables + DAG lineage → catalog → expose as MCP server for AI tools | ⬜ | Enterprise | Flagship AI-native differentiator. |
| **Pushdown-aware profiling** — compute stats in-database, return only the stats | ⬜ | Enterprise | Pairs with catalog + pushdown engines. |
| **Ibis `create_table` in-DB materialization** — write back same DB with zero round-trip | ⬜ | Core (open) | Small addition to the Ibis engine. |
| **Power BI semantic model read** (XMLA/TMDL/DAX) | ⬜ | Enterprise | Read feasible; writing cautioned (governance). |
| **StarRocks Stream Load / MySQL LOAD DATA** — best-tier bulk paths | ⬜ | Core (open) | Optimizes the existing `fast_write` dispatcher. |
| **`fast_read`** (ConnectorX Arrow-native fast source reads) | ⬜ | Core (open) | Symmetric to fast_write. |
| **Resume from failed node** — scheduled retries re-run only what failed | ⬜ | Core (open) | Today a retry re-runs the whole flow; writes must be idempotent. |
| **Automated tests + CI** | 🟡 | Core (open) | pytest suite in `backend/tests` (run with `pytest`); CI not set up yet. |
| **Desktop app** (Electron/Tauri) — education/free tier | ⬜ | Packaging | Independent track. |
| **Git integration for flows** — flows as files in the organisation's Git repository | ⬜ | Enterprise | Saving stays local and frequent; publishing is deliberate: **Push to Git**, used when a set of changes is finished, shows a diff of everything changed since the last push and asks for a commit message. Pull to update from the remote, branch-per-environment promotion (development → test → production), review through pull requests. Depends on deterministic serialization (done, v1.37). |
| **Spark pushdown** — run work on the organisation's existing Spark | ⬜ | Enterprise | A PySpark engine whose node code executes on a remote cluster through Spark Connect and returns results as Arrow, plus Spark as an Ibis backend. Results written inside the cluster by default, with Explore-style row caps when pulled back; Kerberos authentication for Hadoop environments. Plain Spark SQL through a Thrift Server dialect may work with the existing SQL pushdown engine and stays in Community. |

---

## Free vs enterprise: the intended line

**Free (open core):** a complete tool for an individual or a team — every
engine, including SQL and Ibis pushdown into the warehouse, **all** connectors,
fast_write, reports, profiling, exploration (including live database
exploration), AI authoring, scheduling with retries, alerts and calendar rules,
the plugin system and isolated plugin runtime, and self-hosting.

Out-of-core processing and single-machine parallelism are deliberately **free**:
the individual with a laptop and a file bigger than their RAM is exactly who
needs them. What organizations pay for is scale *across* machines and users.

**Enterprise (paid):** what organizations need to run SFS for many people —
licensing, authentication and SSO, RBAC, worker-queue scaling across machines,
the semantic catalog / MCP layer, and support. The theme: **individuals get a
great tool; companies pay for security, scale, governance and support.**

Connectors are deliberately **never** gated — gating them would just fork the
community, and open connectors are table stakes.

---

## Repository split

1. ✅ **Engine-registry refactor** — `executor.py` dispatches through
   `app/engine/registry.py`; builtins register like any plugin.
2. ✅ **Plugin loader** — entry-point group `sfs.plugins`, plus
   `/api/capabilities` so the frontend renders engines dynamically.
3. ✅ **Enterprise repository** — `sfs-enterprise` (private) holds the
   `sfs_enterprise` plugin package; it registers nothing yet.
4. ⬜ **Licensing library** — Ed25519 sign/verify, offline; the first enterprise
   feature.
5. ⬜ **Auth interface** — no-op default in open core; implementation in the
   enterprise plugin.
