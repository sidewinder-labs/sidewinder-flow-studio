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
user's configured connections.

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

## Discovering what's installed

`GET /api/capabilities` returns registered engines (with `source`, `tier` and
availability), registered templates and examples, and the load status of every discovered
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
