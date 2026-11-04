"""
Engine registry + plugin loader.

SFS execution engines (pandas, Polars, DuckDB, SQL, Ibis, and any third-party/enterprise
engine) are described by an `EngineSpec` and registered here. The executor
dispatches through this registry instead of a hard-coded if/elif chain, so new
engines can be added by external packages without touching core code.

Plugins are discovered via Python entry points in the group ``sfs.plugins``:

    # in a plugin package's pyproject.toml
    [project.entry-points."sfs.plugins"]
    my_engine = "my_package.plugin:register"

The referenced callable is invoked at startup with the public registry API:

    def register(api):
        api.register_engine(EngineSpec(name="my_engine", ..., run=my_run))
        api.register_template(kind="chart", name="My plot", code="...")
        api.register_example(name="My example", flow={"nodes": [...], "edges": [...]})

See docs/PLUGINS.md for the full contract.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Callable

log = logging.getLogger("sfs.registry")

ENTRY_POINT_GROUP = "sfs.plugins"


@dataclass
class EngineContext:
    """Everything an engine's `run` callable may need. Which fields are
    populated depends on the engine's declared `input_mode`."""
    code: str                              # the node's code cell
    node_data: dict                        # full node data (labels, options…)
    exec_globals: dict                     # injected names (pd, get_engine, …)
    preamble: str = ""                     # flow imports + shared functions
    params: dict = field(default_factory=dict)  # declared params, defaults applied
    # Where this node's output will be cached. An engine MAY stream its result
    # straight here (and declare output_mode="file") instead of returning a
    # frame, which avoids materialising the result in memory.
    output_path: str | None = None
    # input_mode="pandas"
    df_in: Any = None
    df_extra: list = field(default_factory=list)
    # input_mode="arrow"
    arrow_in: Any = None
    arrow_extra: list = field(default_factory=list)
    # input_mode="paths" (lazy scanning, e.g. Polars)
    frame_paths: list[str] = field(default_factory=list)
    # pushdown engines
    target_url: str | None = None
    target_dialect: str | None = None


@dataclass
class EngineSpec:
    """Declarative description of an execution engine."""
    name: str                              # id used in node data ("pandas")
    label: str                             # UI label ("pandas")
    description: str = ""                  # UI subtitle
    run: Callable[[EngineContext], Any] | None = None
    tier: str = "free"                     # "free" | "enterprise"
    input_mode: str = "pandas"              # pandas | arrow | paths | none
    output_mode: str = "pandas"             # pandas | arrow
    language: str = "python"                # editor language hint: python | sql
    requires: tuple[str, ...] = ()          # importable modules this engine needs
    needs_target_connection: bool = False   # pushdown engines
    hint: str = ""                          # placeholder/help text
    # Optional declarative parameters. If present, the node panel renders form
    # fields above the code editor and their values are delivered in
    # EngineContext.params. Engines that declare none stay code-cell-only.
    # Each entry: {key, label, type, default?, required?, options?, help?}
    # type ∈ string | text | number | boolean | select | column | connection
    # (text: a multi-line field, e.g. SQL)
    params: tuple[dict, ...] = ()
    # Optional "View as Python": to_code(params, inputs) -> str returns plain
    # pandas-node code doing the same work as the form. `params` are the
    # resolved declared params; `inputs` the upstream frames from the last run
    # (pandas, possibly empty). Raise ValueError with a message the user can act
    # on when code can't be generated yet.
    to_code: Callable[[dict, list], str] | None = None
    source: str = "builtin"                 # "builtin" or "plugin:<dist>"

    def resolve_params(self, node_data: dict) -> dict:
        """Pull this engine's declared params out of a node's data, applying
        defaults. Unknown keys in node_data are ignored, so params never
        collide with core node fields."""
        out: dict = {}
        for p in self.params:
            key = p.get("key")
            if not key:
                continue
            val = node_data.get(key, None)
            if val in (None, ""):
                val = p.get("default")
            if p.get("type") == "number" and val not in (None, ""):
                try:
                    val = float(val)
                    if val == int(val):
                        val = int(val)
                except (TypeError, ValueError):
                    pass
            if p.get("type") == "boolean":
                val = bool(val)
            out[key] = val
        return out

    def missing_required(self, params: dict) -> list[str]:
        """Names of required params with no value — checked before running."""
        return [p["label"] if p.get("label") else p["key"]
                for p in self.params
                if p.get("required") and params.get(p.get("key")) in (None, "", [])]

    def available(self) -> tuple[bool, str | None]:
        """Is this engine usable in the current environment?"""
        import importlib.util
        for mod in self.requires:
            if importlib.util.find_spec(mod) is None:
                return False, f"missing package '{mod}'"
        return True, None


ENGINES: dict[str, EngineSpec] = {}
_PLUGINS_LOADED = False
_LOADED_PLUGINS: list[dict] = []


def register_engine(spec: EngineSpec, *, override: bool = False) -> None:
    if spec.name in ENGINES and not override:
        log.warning("Engine '%s' already registered — ignoring duplicate", spec.name)
        return
    ENGINES[spec.name] = spec
    log.info("Registered engine '%s' (%s, %s)", spec.name, spec.tier, spec.source)


def get_engine_spec(name: str | None) -> EngineSpec:
    """Look up an engine, defaulting to pandas. Unknown names fall back to
    pandas so an old flow referencing a removed plugin still runs."""
    if not name:
        return ENGINES["pandas"]
    spec = ENGINES.get(name)
    if spec is None:
        log.warning("Unknown engine '%s' — falling back to pandas", name)
        return ENGINES["pandas"]
    return spec


# ── Templates ─────────────────────────────────────────────────────────────────
# Starting code a plugin offers for a node type. The node panel lists them in a
# dropdown; picking one copies the code into the editor, where the user owns it
# (nothing stays linked to the plugin).

TEMPLATE_KINDS = ("chart",)
TEMPLATES: list[dict] = []


def register_template(*, kind: str, name: str, code: str, description: str = "",
                      source: str = "builtin") -> None:
    if kind not in TEMPLATE_KINDS:
        raise ValueError(f"Unknown template kind '{kind}'; supported: {', '.join(TEMPLATE_KINDS)}")
    if any(t["kind"] == kind and t["name"] == name for t in TEMPLATES):
        log.warning("Template '%s' (%s) already registered — ignoring duplicate", name, kind)
        return
    TEMPLATES.append({"kind": kind, "name": name, "code": code,
                      "description": description, "source": source})
    log.info("Registered %s template '%s' (%s)", kind, name, source)


def list_templates(kind: str | None = None) -> list[dict]:
    return [dict(t) for t in TEMPLATES if kind is None or t["kind"] == kind]


# ── Examples ──────────────────────────────────────────────────────────────────
# Ready-made flows a plugin ships (sample data + nodes). The project picker
# lists them; picking one creates a new project holding a copy of the flow.

EXAMPLES: list[dict] = []


def register_example(*, name: str, flow: dict, description: str = "",
                     source: str = "builtin") -> None:
    if not isinstance(flow, dict) or not isinstance(flow.get("nodes"), list):
        raise ValueError(f"Example '{name}': flow must be a dict with a 'nodes' list")
    if any(e["name"] == name for e in EXAMPLES):
        log.warning("Example '%s' already registered — ignoring duplicate", name)
        return
    EXAMPLES.append({"name": name, "description": description, "flow": flow, "source": source})
    log.info("Registered example '%s' (%s)", name, source)


def list_examples() -> list[dict]:
    """Names and descriptions only; get_example() returns the flow."""
    return [{k: e[k] for k in ("name", "description", "source")} for e in EXAMPLES]


def get_example(name: str) -> dict | None:
    return next((e for e in EXAMPLES if e["name"] == name), None)


# ── Helpers ───────────────────────────────────────────────────────────────────
# Functions a plugin adds to node code (like fast_read). Each is injected into
# every node's scope and listed in the in-app help, from its signature and
# docstring.

HELPERS: dict[str, dict] = {}


def register_helper(*, name: str, func: Callable, category: str = "Plugins",
                    examples: tuple[dict, ...] = (), source: str = "builtin") -> None:
    if not name.isidentifier() or name.startswith("_"):
        raise ValueError(f"Helper name '{name}' must be a public Python identifier")
    if not callable(func):
        raise ValueError(f"Helper '{name}': func must be callable")
    if name in HELPERS:
        log.warning("Helper '%s' already registered — ignoring duplicate", name)
        return
    HELPERS[name] = {"func": func, "category": category, "examples": tuple(examples), "source": source}
    log.info("Registered helper '%s' (%s)", name, source)


def list_engines() -> list[dict]:
    """Serializable engine list for /api/capabilities."""
    out = []
    for spec in ENGINES.values():
        ok, reason = spec.available()
        out.append({
            "name": spec.name,
            "label": spec.label,
            "description": spec.description,
            "tier": spec.tier,
            "language": spec.language,
            "needs_target_connection": spec.needs_target_connection,
            "params": list(spec.params),
            "hint": spec.hint,
            "has_to_code": spec.to_code is not None,
            "source": spec.source,
            "available": ok,
            "unavailable_reason": reason,
        })
    return out


class RegistryAPI:
    """The stable surface handed to plugins. Keeping this narrow means plugin
    code doesn't reach into SFS internals that may change."""
    EngineSpec = EngineSpec
    EngineContext = EngineContext

    def __init__(self, source: str):
        self._source = source

    @staticmethod
    def run_isolated(**kwargs):
        """Run work in a separate interpreter (its own venv) exchanging Arrow
        files — see app/engine/runtime.py. Use this when your dependency would
        conflict with SFS's own environment. Imported lazily so the registry
        stays importable in minimal contexts."""
        from app.engine.runtime import run_isolated as _ri
        return _ri(**kwargs)

    @staticmethod
    def venv_python(venv: str):
        from app.engine.runtime import venv_python as _vp
        return _vp(venv)

    def register_engine(self, spec: EngineSpec, *, override: bool = False) -> None:
        spec.source = self._source
        register_engine(spec, override=override)

    def register_example(self, *, name: str, flow: dict, description: str = "") -> None:
        """Ship a ready-made flow: {name?, nodes, edges, imports?} in the saved
        project format. Users start a new project from it."""
        register_example(name=name, flow=flow, description=description, source=self._source)

    def register_helper(self, name: str, func: Callable, *, category: str = "Plugins",
                        examples: tuple[dict, ...] = ()) -> None:
        """Add a function to node code, e.g. api.register_helper("my_helper", fn).
        It appears in every node's scope and in the help panel, documented by
        its signature and docstring. examples: ({"title", "engine", "code"}, ...)."""
        register_helper(name=name, func=func, category=category, examples=examples,
                        source=self._source)

    def register_template(self, *, kind: str, name: str, code: str, description: str = "") -> None:
        """Offer starting code for a node type (today: kind="chart")."""
        register_template(kind=kind, name=name, code=code, description=description,
                          source=self._source)


def load_plugins() -> list[dict]:
    """Discover and initialise plugins declared under the `sfs.plugins` entry
    point group. A failing plugin is logged and skipped — it must never take
    the application down."""
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return _LOADED_PLUGINS
    _PLUGINS_LOADED = True

    # SFS's own examples first, so they lead the picker's list
    from app import examples
    examples.register()

    try:
        eps = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:                     # pragma: no cover
        log.warning("Could not read entry points: %s", exc)
        return _LOADED_PLUGINS

    for ep in eps:
        info = {"name": ep.name, "value": ep.value, "loaded": False, "error": None}
        try:
            hook = ep.load()
            hook(RegistryAPI(source=f"plugin:{ep.name}"))
            info["loaded"] = True
            log.info("Loaded plugin '%s'", ep.name)
        except Exception as exc:
            info["error"] = str(exc)
            log.error("Plugin '%s' failed to load: %s", ep.name, exc)
        _LOADED_PLUGINS.append(info)

    return _LOADED_PLUGINS


def loaded_plugins() -> list[dict]:
    return _LOADED_PLUGINS
