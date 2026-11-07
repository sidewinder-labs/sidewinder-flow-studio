# Sidewinder Flow Studio (SFS) — Installation Guide

SFS is installed one of two ways:

1. **Desktop app** — one person on one computer. An installer for Windows and Linux; no Docker, Python or terminal.
2. **Docker** — servers and teams. Either a **single container**, or **two to three containers** (backend, frontend and the optional report viewer).

Both are built and tested for every release. They share the same project format, so you can move work between them with export and import.

---

## Desktop app

Download the installer from [sidewinderlabs.dev/download](https://sidewinderlabs.dev/download/) and run it. The app starts its own backend, keeps your data in your user folder, and updates itself.

- **Data** lives in the OS app-data folder (Linux: `~/.local/share/dev.sidewinderlabs.flowstudio/`, Windows: `%APPDATA%\dev.sidewinderlabs.flowstudio\`). About (click the FS logo) → *Where is my data* opens it.
- **Back up `secret.key`** in that folder: it encrypts your saved connection passwords.
- **Drivers built in**: PostgreSQL, MySQL/MariaDB (and StarRocks, Doris), SQL Server, Oracle, ClickHouse, Trino and SQLite. Snowflake, BigQuery, Redshift, Databricks, Druid, Azure Data Lake, Delta and Iceberg need Docker.
- **Plugins built in**: curve fitting, IBM Planning Analytics (TM1), Power BI refresh.
- **Settings** (the optional variables listed under [Settings](#settings)) go in a `.env` file in the data folder.
- A database on your own computer is at `localhost`.

Windows may show a SmartScreen warning while code signing is being set up: *More info → Run anyway*. See the [code signing policy](https://sidewinderlabs.dev/code-signing/). More detail: [desktop/README.md](../desktop/README.md).

---

## Docker

**Needs:** Docker 24+ and Docker Compose v2.

**Generate a SECRET_KEY** (it encrypts stored connection passwords and API keys):

```bash
docker run --rm python:3.13-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```

(`make gen-key` runs the same.) Copy the output, a ~44-character string ending in `=`. Keep it stable: if you change it later, previously saved connection passwords can no longer be decrypted.

### Single container

One image runs the backend and serves the built frontend from the same port. Good for a personal server or a quick trial. The report viewer is not included; use the multi-container setup if you need it.

```bash
git clone https://github.com/sidewinder-labs/sidewinder-flow-studio.git
cd sidewinder-flow-studio
cp .env.docker .env          # then set SECRET_KEY in .env
docker compose -f docker-compose.single.yml up --build -d
# → http://localhost:8000
```

Stop it with `docker compose -f docker-compose.single.yml down`.

### Multi-container (2–3 containers)

- **backend** — FastAPI and the engines (internal, not exposed directly)
- **frontend** — nginx serving the UI and proxying `/api` and `/ws` to the backend (exposed on `:8080`)
- **reports** — the Streamlit report viewer (optional; starts only with a profile flag)

They share a Docker volume, so the reports container reads what the backend writes (read-only: the backend is the single writer).

```bash
cp .env.docker .env          # set SECRET_KEY; optionally FRONTEND_PORT (default 8080)
docker compose up --build -d
# → http://localhost:8080
```

To add the report viewer:

```bash
docker compose --profile reports up --build -d
# → UI on http://localhost:8080, reports on http://localhost:8501
```

`make docker`, `make docker-down`, `make docker-logs` and `make docker-single` do the same.

### Data

- Everything persistent (projects, frame cache, metadata database, reports) lives in the `flow_data` named volume, so it survives restarts and rebuilds. To use a host directory instead, uncomment the `driver_opts` bind block at the bottom of `docker-compose.yml` and point `device:` at your path.
- The metadata database is SQLite in the volume by default. For more than one user, set `METADATA_DB_URL` in `.env` to a PostgreSQL URL (see `.env.docker`).
- **On Linux**, a database running on the host machine is at `host.docker.internal` (the compose files map it), not `localhost`.
- Files you read in node code must be inside the container: mount a host folder (for example at `/data/imports`) in `docker-compose.yml`.

### Choosing drivers

The backend image installs the drivers named in the `SFS_EXTRAS` build setting. It defaults to `all-connectors,pushdown`: every warehouse driver plus the SQL and Ibis engines. For a smaller image, set it in `.env` and rebuild:

```ini
SFS_EXTRAS=trino,pushdown     # comma-separated, from the list below
SFS_EXTRAS=                   # core engines only (pandas, polars, duckdb)
```

| Extra | Adds |
|---|---|
| `snowflake` | Snowflake |
| `bigquery` | BigQuery |
| `clickhouse` | ClickHouse |
| `redshift` | Redshift |
| `mssql` | SQL Server (`pyodbc`; also needs the OS-level Microsoft ODBC Driver 18) |
| `oracle` | Oracle (thin mode; no Oracle client needed) |
| `mysql` | MySQL, StarRocks, Doris |
| `druid` | Druid |
| `trino` | Trino |
| `databricks` | Databricks SQL |
| `adls` | Azure Data Lake storage |
| `deltalake`, `iceberg` | `read_delta()` / `read_iceberg()` in node code |
| `fastread` | ConnectorX, the fastest path for `fast_read` and the Database read node |
| `pushdown` | the SQL ⚡ and Ibis ⚡ engines |
| `all-connectors` | every driver above |

PostgreSQL and SQLite work without an extra. The build log prints which drivers made it into the image:

```
SFS drivers: 14/15 ok: snowflake bigquery clickhouse ... ibis | missing: mssql
```

Then rebuild: `docker compose up --build -d` (or `docker compose -f docker-compose.single.yml up --build -d`).

### Settings

Optional settings are environment variables. In Docker, add them to the `backend` service's `environment:` block in `docker-compose.yml` (or to `docker-compose.single.yml`); in the desktop app, put them in the data folder's `.env`.

```ini
# Explore guardrails: max rows per chart query, query time limit
EXPLORE_ROW_CAP=50000
EXPLORE_TIMEOUT_S=30

# AI agent (or configure providers in the UI: Meta → AI)
ANTHROPIC_API_KEY=

# Run independent branches concurrently (off by default)
SFS_PARALLEL_BRANCHES=0

# DuckDB memory ceiling and spill location. Below the limit DuckDB works in
# memory; above it, it spills instead of failing.
SFS_DUCKDB_MEMORY_LIMIT=
SFS_DUCKDB_TEMP_DIR=

# Where isolated plugin virtual environments live
SFS_PLUGIN_VENVS_DIR=

# Scheduler failure alerts
SLACK_WEBHOOK_URL=
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=sfs@localhost
```

---

## Plugins

Plugins add engines, connection types, chart templates and examples. More come from the **Plugins** menu (puzzle icon, top bar): official, signed packs from the Sidewinder Labs catalog that install on the desktop app and on Docker without rebuilding anything. The rest of this section is about building plugins into a Docker image yourself. **Curve fitting**, **IBM Planning Analytics (TM1)** and **Power BI refresh** are included in the desktop app and the Docker images.

**In Docker**, the image installs the plugins named in `SFS_PLUGINS`, by short name (`sfs-plugin-<name>`). Add one in `.env` and rebuild the backend:

```bash
SFS_PLUGINS=curvefit,tm1,powerbi,sklearn
docker compose build --no-cache backend && docker compose up -d
```

The engine appears in the node picker with a 🧩 badge.

### Isolated plugin environments

A plugin can run in **its own virtual environment**, so its dependencies never touch SFS's. SFS hands the frame over as an Arrow file and never imports the library itself. In Docker, build the environment into the image and point `SFS_PLUGIN_VENVS_DIR` at it:

```dockerfile
RUN python -m venv /opt/sfs-venvs/sklearn \
 && /opt/sfs-venvs/sklearn/bin/pip install --no-cache-dir pyarrow pandas scikit-learn
ENV SFS_PLUGIN_VENVS_DIR=/opt/sfs-venvs
```

Then pick the **scikit-learn (isolated)** engine on a node; its "Plugin environment" field takes the folder name (`sklearn`) or an absolute path.

See [PLUGINS.md](PLUGINS.md) to write your own.

---

## Verifying the install

1. Open the UI (desktop: the app window; Docker: `:8080` multi-container, `:8000` single).
2. Click the **FS** logo (top-left): the About dialog shows the version.
3. Create a project, add a **Processor** after the trigger, put `df = pd.DataFrame({'x':[1,2,3]})` in it, and click **Run Flow**.
4. The processor shows `3 rows`.

For the report viewer (Docker with `--profile reports`), add a **Report** node after a processor, run, then open the link on the node (or browse to `:8501`).

---

## Common issues

| Symptom | Fix |
|---|---|
| Saved connection passwords stop working / `InvalidToken` in the logs | `SECRET_KEY` changed since they were saved (desktop: `secret.key` was replaced). Restore the original key, or edit each connection and re-enter its password. Other connections and flows keep working; only nodes using the affected connection fail. |
| A connection to a database on this computer fails in Docker | Use `host.docker.internal` as the host, not `localhost`. |
| A file path works on your computer but not in a node (Docker) | The node runs in the container: mount the folder and use the container path. |
| Engine errors "sqlglot/ibis not installed" | The image was built without `pushdown` in `SFS_EXTRAS`. Add it and rebuild. |
| Warehouse connection fails on Test | Check its driver is in `SFS_EXTRAS` (the build log lists them); for Snowflake verify the account-identifier format in the Host field. |
| A driver you need isn't in the desktop app | Use Docker, which includes every driver. |
| `IO Error ... file is being used by another process` (Windows) | Keep the data folder out of OneDrive/Dropbox: sync locks corrupt DuckDB files. |

Logs: Docker `docker compose logs backend --tail 100`; desktop About → *Where is my data* → logs.

**Reporting a bug:** click the FS logo → **Copy diagnostics** and paste the result into the [bug report form](https://github.com/sidewinder-labs/sidewinder-flow-studio/issues/new/choose). It lists the SFS version, how it's installed, the OS, plugins and library versions; no connections, paths or secrets.

**Why the builds are reproducible:** the Docker images and the desktop app install every Python package at the version pinned in `locks/requirements.lock`, so the same release builds the same way on any day. Updates to those versions arrive as tested pull requests.
