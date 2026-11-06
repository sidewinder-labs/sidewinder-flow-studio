# Contributing to SFS

Thanks for your interest. SFS is maintained by one person alongside a full-time
job, so a little process keeps things sane for everyone.

## Before you start

- **Open an issue first** for anything beyond a small fix. It saves you building
  something that doesn't fit the direction, and me reviewing something I have to
  decline.
- **Bug reports**: include SFS version (the About dialog / `pyproject.toml`), how
  you're running it (desktop / Docker), the engine and connector involved, and the
  full error text from `docker compose logs backend` or the node's error panel.
- Response times will vary. This is a side project, not a company.

## Development setup

Running from source is for developing SFS. It isn't a supported way to use it —
use the desktop app or Docker ([docs/INSTALLATION.md](docs/INSTALLATION.md)),
and reproduce bugs there before reporting them.

```bash
cd backend && pip install -e ".[all-connectors,pushdown,dev]" && python main.py
cd frontend && npm install && npm run dev
```

## Project conventions

Read **[AGENTS.md](AGENTS.md)** first — it documents the core contracts:

- Only the variable `df` passes between nodes; extra inputs are `df2`, `df3`, `dfs`
- Data is cached between nodes as Apache Arrow IPC
- Heavy dependencies (duckdb, polars, sqlglot, ibis, DB drivers) are **lazily
  imported inside functions**, never at module import time, so SFS starts without
  optional extras installed
- Optional drivers are pyproject extras (`.[clickhouse]`, `.[all-connectors]`)
- Writing to a database in node code uses `fast_write`, not `df.to_sql`

## Adding an engine

Don't modify the executor. Engines are pluggable — see
**[docs/PLUGINS.md](docs/PLUGINS.md)** for the `EngineSpec` contract and the
`sfs.plugins` entry point. New engines should register through that mechanism.

## Adding a connector

Connectors are dialects in `backend/app/services/connection_service.py`:

1. Add the URL construction in `build_url()` (and `get_connect_args()` if the
   driver needs non-URL connection parameters)
2. Add the driver as a pyproject extra, and to `all-connectors`
3. Add the dialect + field hints to `DIALECT_HINTS` in the frontend
   `MetadataPanel.tsx`
4. If it has a native bulk-load path, add it to `fast_write` in `executor.py`

## Pull requests

- Keep PRs focused — one feature or fix
- Explain what you tested and how (SFS has limited automated test coverage; manual
  verification notes are genuinely useful)
- Don't bump version numbers; that happens at release
- Frontend: run `npm run typecheck` before submitting

## Code of conduct

Be decent to each other. Technical disagreement is fine; personal hostility isn't.
