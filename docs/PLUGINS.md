# SFS Plugin Contract

SFS can be extended with **plugins** — separate Python packages that add
execution engines (and, in future, other capabilities) without modifying SFS
itself. Plugins are discovered at startup via Python entry points.

This document is the stable contract. Code written against it should keep
working across SFS releases.

---

## Why plugins exist

Data tooling has a dependency problem: libraries like PyCaret, scikit-learn or
statsmodels pin conflicting versions of numpy/pandas/scikit-learn, and
installing them into a working environment routinely breaks it. SFS's answer is
to keep the ETL core stable and let heavy capabilities live **outside** it,
exchanging data as Apache Arrow.

Two levels of isolation are supported:

1. **In-process plugins** (this document) — a Python package installed
   alongside SFS that registers an engine. Suitable for light dependencies that
   coexist safely with the core.
2. **Isolated-runtime plugins** (planned) — heavy libraries run in their own
   pinned virtual environment/container, exchanging Arrow/parquet files over a
   shared volume, so the core environment is never polluted. The engine
   registered in-process becomes a thin client for that runtime.

The registration contract below is the same for both; only the `run`
implementation differs.

**How plugins reach users.** Plugins written against this contract reach SFS
in three ways:

| Way | For | How |
|---|---|---|
| Bundled | curve fitting, TM1, Power BI | built into the desktop app and the Docker images |
| **Official packs** | anything in the Sidewinder Labs catalog | **Plugins** menu (puzzle icon): install, update, roll back, no rebuild — see [Official plugin packs](#official-plugin-packs) |
| Custom image | your own or third-party plugins, server edition only | a Docker image built with your package installed — see [Custom plugins on the server](#custom-plugins-on-the-server) |

The desktop app loads only bundled plugins and signed official packs.

---

## A complete working example

[`plugins/sfs-plugin-sklearn`](../plugins/sfs-plugin-sklearn) is an installable
package implementing everything below — a scikit-learn engine. Copy it as a
starting point rather than writing a plugin from scratch.

To try it, add it to `SFS_PLUGINS` and rebuild the Docker image (see
[INSTALLATION.md](INSTALLATION.md#plugins)). While developing a plugin, install
it editable into a source checkout (`pip install -e plugins/<your-plugin>`, see
[CONTRIBUTING.md](../CONTRIBUTING.md)); ship it to users through the Docker
image's `SFS_PLUGINS`.

## Declaring a plugin

In your package's `pyproject.toml`:

```toml
[project.entry-points."sfs.plugins"]
my_plugin = "my_package.plugin:register"
```

The referenced callable is invoked once at SFS startup:

```python
# my_package/plugin.py
def register(api):
    api.register_engine(api.EngineSpec(
        name="my_engine",
        label="My Engine",
        description="What it does (shown in the engine picker)",
        run=run_my_engine,
        input_mode="pandas",
        output_mode="pandas",
        language="python",
        tier="free",
        requires=("my_dependency",),
    ))
```

A plugin that raises during `register` is logged and skipped — it can never
prevent SFS from starting.

---

## EngineSpec fields

| Field | Type | Meaning |
|---|---|---|
| `name` | str | Stable id stored in node data. Must be unique. |
| `label` | str | Shown in the engine picker. |
| `description` | str | One-line subtitle in the picker. |
| `run` | callable | `run(EngineContext) -> DataFrame \| pa.Table`. |
| `input_mode` | str | `pandas` \| `arrow` \| `paths` \| `none` (below). |
| `output_mode` | str | `pandas` or `arrow` — what `run` returns. |
| `language` | str | `python` or `sql` — editor syntax highlighting. |
| `tier` | str | `free` or `enterprise` (informational/UI). |
| `requires` | tuple[str] | Importable module names. If missing, the engine shows as unavailable instead of erroring at run time. |
| `needs_target_connection` | bool | If true, the UI shows a connection picker and `target_url`/`target_dialect` are populated. |
| `hint` | str | Optional placeholder/help text. For a plugin engine it is the code editor's placeholder. |
| `params` | tuple[dict] | Optional form fields (see *Declaring parameters*). |
| `to_code` | callable | Optional *View as Python*: `to_code(params, inputs) -> str` (see below). |

### `input_mode` — what SFS prepares for you

| Mode | Populated fields | Use when |
|---|---|---|
| `pandas` | `df_in`, `df_extra` | You want ready pandas DataFrames. |
| `arrow` | `arrow_in`, `arrow_extra` | You want zero-copy Arrow tables. |
| `paths` | `frame_paths` | You want to scan the cached Arrow IPC files lazily yourself. |
| `none` | — | Pushdown engines that don't consume upstream frames. |

The first upstream node maps to `df_in`/`arrow_in`; additional upstream nodes
land in `df_extra`/`arrow_extra` (in stable parent order). This mirrors the
`df`, `df2`, `df3` convention users see in node code.

---

## Declaring parameters (optional)

By default a node's only input is its code cell. An engine can also declare
`params` — form fields the node panel renders above the editor. Values are
stored in the node's data and delivered to `run` in `ec.params` with defaults
already applied.

```python
params=(
    {"key": "target", "label": "Target column", "type": "column",
     "help": "Leave the code cell empty to just train on this target."},
    {"key": "kind", "label": "Model", "type": "select",
     "options": ["auto", "rf_classifier", "linear"], "default": "auto"},
    {"key": "test_size", "label": "Test size", "type": "number", "default": 0.2},
    {"key": "show_metrics", "label": "Print metrics", "type": "boolean", "default": True},
)
```

| Field | Meaning |
|---|---|
| `key` | Stored in node data and used as the key in `ec.params`. |
| `label` | Field label (defaults to `key`). |
| `type` | `string` \| `text` (multi-line, monospaced: SQL and the like) \| `number` \| `boolean` \| `select` \| `column` \| `connection`. Unknown types render as text. |
| `default` | Applied when the field is empty. |
| `required` | Run fails early with a clear message if empty. |
| `options` | Choices for `select`: strings, or `{"value", "label", "group"}` objects. Options that share a `group` are listed under that heading, in first-seen order. `value` is what is stored. |
| `help` | Small hint under the field. |

`column` renders a dropdown of the upstream frame's actual columns once that
node has run, and degrades to a text input before then. `connection` lists the
user's configured SQL connections; add `"dialects": ["tm1"]` to list only
connections of your own connection type instead (see below).

Params are **optional and additive** — declare none and your engine behaves
exactly as before, a plain code cell. Declaring them doesn't remove the code
cell either: a user can fill the fields, write code, or both (the example plugin
exposes the chosen target to node code as `TARGET`).

Unknown keys in node data are ignored by `resolve_params`, so params can never
collide with core node fields.

---

## EngineContext

```python
@dataclass
class EngineContext:
    code: str              # the node's code cell
    node_data: dict        # full node data (custom options live here)
    exec_globals: dict     # injected names: pd, sa, get_engine, fast_write, vars, …
    preamble: str          # flow-level imports + shared functions
    params: dict           # declared params, defaults applied
    df_in / df_extra       # input_mode="pandas"
    arrow_in / arrow_extra # input_mode="arrow"
    frame_paths            # input_mode="paths"
    target_url             # needs_target_connection=True
    target_dialect         # needs_target_connection=True
```

Custom per-node options: read them from `node_data`. (UI fields for custom
options are not yet pluggable — for now use conventions or the code cell.)

---

## Writing `run`

```python
def run_my_engine(ec):
    # Honour the flow preamble so shared functions/imports are available
    scope = dict(ec.exec_globals)
    scope["df"] = ec.df_in
    if ec.preamble.strip():
        exec(ec.preamble, scope)
    exec(ec.code, scope)
    return scope["df"]          # must match output_mode
```

Rules:

1. **Return type must match `output_mode`** (`pandas.DataFrame` or
   `pyarrow.Table`). SFS caches it as Arrow either way.
2. **Handle empty code** — a node with no code should pass its input through
   rather than fail.
3. **Import heavy dependencies inside `run`**, never at module import time, so
   SFS starts even when the dependency is absent.
4. **Raise informative exceptions** — the message is shown on the node.
5. **Don't mutate `ec.exec_globals`** — copy it first (as above).

---

## Isolated runtime

For libraries whose pins would fight SFS's own environment (PyCaret is the usual
offender), run the work in a **separate interpreter** — its own virtual
environment — exchanging Arrow files. SFS never imports the library.

Your engine's `run` becomes a thin client:

```python
def run_my_engine(ec):
    table, info = api.run_isolated(
        venv="mylib",                    # folder under SFS_PLUGIN_VENVS_DIR, or a path
        runner=_runner_path(),           # a script shipped with YOUR package
        inputs=[ec.df_in],
        params=ec.params,
        code=ec.code,
    )
    return table                          # declare output_mode="arrow"
```

Create the environment once, with whatever versions the library demands:

```bash
python -m venv $SFS_PLUGIN_VENVS_DIR/mylib
$SFS_PLUGIN_VENVS_DIR/mylib/bin/pip install pyarrow pandas mylib==1.2.3
```

### Runner contract

SFS writes a job directory and invokes `<venv>/bin/python <runner.py> <request.json>`:

| File | Contents |
|---|---|
| `input.arrow`, `input2.arrow`, … | upstream frames (Arrow IPC) |
| `request.json` | `{inputs[], output, params, code, meta}` |
| `output.arrow` | **your runner writes this** |

The runner prints one JSON line when done:

```json
{"ok": true, "rows": 1234, "log": "optional"}
{"ok": false, "error": "target column missing"}
```

Everything else it prints is captured and shown as node logs, so `print()` still
works. Failures — a crash, a timeout, a missing output frame, no JSON response —
all surface as a clear message on the node. Job directories are removed
afterwards.

`plugins/sfs-plugin-sklearn` ships both variants: `sklearn` (in-process) and
`sklearn_isolated` (separate venv), sharing one `runner.py`. Compare them for a
concrete example.

---

## Data contract

Between nodes, SFS passes exactly one frame, cached as an **Arrow IPC file**.
Whatever your engine returns becomes that frame. The user-facing convention is
that node code assigns its result to `df`; engines that execute user code
should follow it.

For isolated-runtime plugins, the recommended handoff is: write the input frame
to a shared volume as Arrow/parquet, invoke the runtime, read the result back.
The core process never imports the heavy library.

---

## View as Python (optional)

A form-driven engine can show its work as ordinary code. Give the `EngineSpec`
a `to_code(params, inputs) -> str` and the processor panel gains two buttons:

- **View as Python** — the code, read-only, with a Copy button
- **Convert to code node** — switches the node to the pandas engine with that
  code in its cell (after asking); the form values stay in the node data but
  are no longer used

`params` are the node's declared params with defaults applied, exactly as
`run` receives them in `ec.params`. `inputs` are the upstream frames from the
last run (pandas DataFrames, in no guaranteed order, possibly empty if the flow
hasn't run): use them only when the code depends on what arrives, and raise
`ValueError` with a message the user can act on ("Run the flow once first…")
when it can't be generated yet.

The code runs in a pandas node, so it gets `df`, `df2`, …, `dfs` and `pd`, and
must leave its result in `df`. Make it produce **the same frame as the engine**
and test that by executing it next to `run` on the same inputs; the simplest
way to keep them from drifting apart is for the engine to execute or share the
very source text it shows. The node card and panel find out through
`has_to_code` in `/api/capabilities`. Endpoint: `POST /api/capabilities/to-code`
with `{engine, node_data, project_id, parent_ids}`.

## Templates (optional)

A plugin can offer starting code for a node type. Today the only kind is
`chart`: the Chart node panel lists every registered chart template in a
**Start from a template** dropdown, and picking one copies its code into the
editor (asking first if there is code already). The copy belongs to the user;
nothing stays linked to the plugin, so later plugin updates don't change
existing charts.

```python
def register(api):
    api.register_template(
        kind="chart",
        name="Fit with residuals",          # shown in the dropdown; unique per kind
        description="Data, fitted curve and residuals",   # tooltip
        code=TEMPLATE_CODE,                 # ordinary Chart node code
    )
```

The code runs like any Chart node code: `df`, `df2`, … and `dfs` hold the
inputs, and it sets `result` or draws with matplotlib. Inputs arrive ordered by
parent node id, which users don't control, so a template that takes several
inputs should identify each by its columns. Keep templates as `.py` files in
your package (read them with `importlib.resources`) so they can be linted and
tested. An unknown `kind` raises `ValueError`; a duplicate name is ignored
with a warning. Guard the call with `hasattr(api, "register_template")` if
your plugin also supports older SFS versions.

## Examples (optional)

A plugin can ship ready-made flows. The project picker lists them under
**Start from an example**; picking one creates a new project holding a copy.

```python
def register(api):
    api.register_example(
        name="My plugin · Getting started",   # unique; shown in the picker
        description="What it shows",           # tooltip
        flow={"name": "Getting started", "nodes": [...], "edges": [...]},
    )
```

`flow` uses the saved project format (`nodes` with `id`, `type`, `position`,
`data`; `edges` with `source`, `target`, `sourceHandle: "out"`,
`targetHandle: "in"`; optional `imports`). Include a trigger node, or one is
added. Ship sample data as package data and read it in the first node with
`importlib.resources`, so the example works in any install, Docker included.
Test that each example runs through the executor.

## Helpers (optional)

A plugin can add functions to node code, like the built-in `fast_read`. The
helper is injected into every node's scope (pandas and Polars nodes, Chart
nodes) and appears in the in-app help under **SFS helpers**, documented from
its own signature and docstring:

```python
def score(df, model: str = "default"):
    """Score each row with a trained model.

    Args:
        df: the rows to score.
        model: which model to use.

    Returns:
        pandas.DataFrame with a score column

    Example:
        df = score(df, model="churn")
    """
    ...

def register(api):
    api.register_helper("score", score, category="Machine learning")
```

Write the docstring in that layout (a one-line summary, then `Args:`,
`Returns:` and `Example:` sections): users read it in the help panel. Names
must be public identifiers, and a built-in helper of the same name wins.
Guard the call with `hasattr(api, "register_helper")` to support SFS before 1.35.

## Connection types (optional)

Parameters live in the flow file: plain text, and part of every export. A
plugin that needs credentials for a system that isn't a SQL database (a
planning server, a BI service, an API) registers a **connection type**
instead. Its connections are created in **Meta → Connections** like any other,
kept in SFS's connection store, and their `secret` fields are encrypted with
the same key as database passwords.

```python
def register(api):
    api.register_connection_type(
        dialect="tm1", label="IBM Planning Analytics (TM1)",
        description="Shown above the fields in the connection editor.",
        fields=[
            {"key": "address",  "label": "Server address", "type": "string", "required": True},
            {"key": "port",     "label": "HTTP port",      "type": "number"},
            {"key": "ssl",      "label": "Use SSL",        "type": "boolean", "default": True},
            {"key": "auth",     "label": "Authentication", "type": "select", "default": "basic",
             "options": [{"value": "basic", "label": "TM1 security"}, {"value": "cam", "label": "CAM"}]},
            {"key": "password", "label": "Password",       "type": "secret"},
            {"key": "namespace", "label": "CAM namespace", "type": "string",
             "show_when": {"auth": ["cam"]}},
        ],
        test=test_fn,   # (fields) -> (ok: bool, message: str); backs the Test button
    )
    api.register_engine(api.EngineSpec(
        name="tm1_read", ..., run=run_read,
        params=({"key": "connection", "label": "Connection", "type": "connection",
                 "dialects": ["tm1"], "required": True}, ...)))


def run_read(ec):
    fields = API.get_connection(ec.params["connection"], "tm1")   # keep `api` from register()
    # {"name": "plan", "dialect": "tm1", "address": ..., "port": 8010, "ssl": True, "password": "…"}
```

| Field | Meaning |
|---|---|
| `dialect` | Lower-case id stored with each connection. Permanent: never rename it. |
| field `type` | `string`, `number`, `boolean`, `select` or **`secret`**. |
| `default`, `required`, `options`, `help` | As for engine params. |
| `show_when` | `{"other_key": [values]}`: show the field only when another field has one of these values (e.g. per authentication mode). |

- **Secrets**: a `secret` field's value is encrypted at rest, never sent back to
  the browser (the editor shows *stored: leave blank to keep*), and left out of
  *Export connections*. An imported connection is marked *needs credentials*
  until the user enters it. Option names that look secret (`token`, `key`,
  `password`…) are treated the same way even if declared `string`.
- **`api.get_connection(name, dialect)`** returns the fields with secrets
  decrypted and defaults applied, read when your node runs. It raises
  `ValueError` with a message for the node when the connection doesn't exist,
  is of another dialect, or still lacks credentials, so a broken connection
  fails only the nodes that use it.
- **Test**: your `test(fields)` runs in a worker thread; return `(True, "Connected
  to …")` or `(False, reason)`, or raise: the exception text is shown.
- These connections are not SQL databases: Explore doesn't list them, and
  `get_engine()` on one says so.
- `api.data_dir()` is the folder SFS keeps its data in, for plugin state such
  as backups (use a subfolder named after your plugin).

Real examples: `plugins/sfs-plugin-tm1` and `plugins/sfs-plugin-powerbi`.

---

## Official plugin packs

The **Plugins** menu installs plugins from the Sidewinder Labs catalog, on the
desktop app (which has no pip) and on Docker (without rebuilding the image).
Only packs signed by Sidewinder Labs install; they are offered only in versions
built and tested for the running SFS version and platform.

### Pack format

A pack is a zip:

```
manifest.json    {"id", "name", "version", "edition": "community"|"enterprise", "description",
                  "sfs_min", "sfs_max", "platform", "python_abi", "register": "module:function",
                  "dependencies": [{"name", "version"}], "sha256", "licence_feature"?}
signature.json   {"kid", "signature"}: Ed25519 over the exact bytes of manifest.json
site/            the plugin package and its own dependencies, importable as-is
```

- `sha256` hashes every file under `site/` (relative path and content, sorted),
  so the signature covers the whole pack.
- `platform` is the app flavour and the OS: `desktop-windows-x64`,
  `desktop-linux-x64`, `server-linux-x64`, `server-linux-arm64`,
  `desktop-macos-arm64`. The desktop app and the Docker image bundle different
  libraries, so each gets its own build.
- `python_abi` is the app's exact Python, e.g. `cp313`.
- `id` is the plugin's `sfs.plugins` entry point name. A pack with the id of a
  bundled plugin and a newer version replaces it; first runs stay offline with
  the bundled one.

**The rule that prevents breakage: a pack never contains a distribution the
app provides** (NumPy, pandas, PyArrow…), it uses the app's. Each app build
exports what it provides (`python -m app.packs.tools bundled`: the Docker image
keeps it at `/opt/sfs-lock/bundled-distributions.json`, desktop releases attach
`bundled-distributions-<platform>.json`). The pack builder resolves the
plugin's dependencies pinned to those versions, leaves out everything the app
has, and refuses a pack that still contains one; SFS refuses such a pack at
install too.

Server packs are built against the default image (`SFS_EXTRAS=all-connectors,pushdown`).
On an image built with fewer extras a pack may miss a library: it then shows as
failed in the Plugins menu, with the import error, and SFS keeps running.

### Trust and safe startup

- Signatures are verified **before install and at every start**: a pack whose
  files changed on disk is disabled with that reason.
- The catalog is signed too and carries `generated_at`; SFS refuses a catalog
  older than the newest it has seen, so an old one can't be replayed to offer
  withdrawn versions. The last good catalog is cached: the menu works offline,
  and installed plugins never need the network.
- Installed at `<data folder>/plugins/<id>/<version>/`; the previous version is
  kept, so **Roll back** is immediate.
- **A plugin that fails to load is disabled, not fatal**: SFS starts and the
  Plugins menu shows it as failed, with the error. If SFS dies while a plugin
  loads, that plugin is disabled at the next start.
- **Safe mode** loads no plugins at all (bundled ones included): *Plugins → Next
  start: safe mode*, `SFS_SAFE_MODE=1` (Docker), or starting the desktop app with
  `--safe-mode`.
- After an app update, each installed pack is re-checked: one that doesn't
  support the new version is disabled with a notice, and **Updates** offers a
  compatible version when the catalog has one.
- Enterprise packs download only with a valid licence (the catalog checks it),
  and check it again when they register.
- Changes apply at the next start. On Docker and the desktop app, **Restart
  now** restarts the backend.

### Building, signing and publishing

`.github/workflows/plugin-packs.yml` (and its twin in `sfs-enterprise`):

1. For each target, install the released app's environment from its lock,
   export what it provides, build each plugin's pack against it, and run the
   plugin's tests with the pack first on the path, in that environment.
2. Only if every build passes, in the GitHub environment `plugin-signing`
   (required reviewer): sign the packs, merge them into the catalog, sign the
   catalog, and upload both to the R2 bucket the catalog Worker serves
   (`catalog/worker`).

By hand: `python -m app.packs.tools build|sign|catalog|bundled` (run from
`backend/`; `--help` on each).

**The plugin-signing key** is separate from the licence key. Its private half
lives only in the `plugin-signing` environment secret `PLUGIN_SIGNING_KEY`
(key id in the variable `PLUGIN_SIGNING_KID`); its public half is in
`app/packs/keys.py`. Generate with `python -m app.packs.tools keygen --out
<file>`, paste the private key into the secret, then delete the file and keep
an offline backup. To rotate: add the new public key to `keys.py` under a new
id, release, switch the secret and variable, re-sign; remove the old key once
no supported release needs it (immediately, if it leaked).

---

## Custom plugins on the server

Plugins of your own, or from third parties, aren't in the catalog. On the
server edition, build an image with them installed. In `.env`:

```ini
SFS_PLUGINS=curvefit,tm1,powerbi,mine     # folders plugins/sfs-plugin-<name> in your checkout
```

then `docker compose build --no-cache backend && docker compose up -d`. Or
derive from the SFS image:

```dockerfile
FROM flow-studio-backend:latest
USER root
COPY my-plugin/ /opt/my-plugin/
RUN pip install --no-cache-dir -c /opt/sfs-lock/requirements.lock /opt/my-plugin
USER flowstudio
```

Installing with `-c /opt/sfs-lock/requirements.lock` keeps the app's library
versions; a plugin whose dependencies conflict with them fails the build
instead of breaking SFS. The desktop app doesn't load custom plugins.

---

## Discovering what's installed

`GET /api/capabilities` returns registered engines (with `source`, `tier` and
availability), connection types, registered templates and examples, and the load status of every discovered
plugin. The frontend
renders the engine picker from this, so a newly installed plugin appears in the
UI with no frontend change. Engines whose `requires` are missing appear greyed
out with the reason.

---

## Versioning and stability

- `EngineSpec`, `EngineContext` and the `register(api)` signature are the
  stable surface; new optional fields may be added over time.
- Unknown engine names in saved flows fall back to pandas with a warning rather
  than failing, so uninstalling a plugin doesn't break existing projects.
- Duplicate engine names are rejected (first registration wins) unless
  registered with `override=True`.
