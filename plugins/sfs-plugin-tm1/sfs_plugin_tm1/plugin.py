"""SFS plugin: IBM Planning Analytics (TM1).

Registers the `tm1` connection type and two engines:
  tm1_read  (TM1 read)  — an MDX query or a cube view, one row per cell
  tm1_load  (TM1 load)  — write the incoming frame into a cube

Engine names are saved into flows: never rename them. TM1py is imported only
when a node runs, so SFS starts without it (the engines show as unavailable).
"""
from __future__ import annotations

from . import connection

_API = None

READ_PARAMS = (
    {"key": "connection", "label": "Connection", "type": "connection", "dialects": ["tm1"], "required": True},
    {"key": "mode", "label": "Read", "type": "select", "default": "mdx", "required": True,
     "options": [{"value": "mdx", "label": "An MDX query"}, {"value": "view", "label": "A cube view"}]},
    {"key": "mdx", "label": "MDX", "type": "text",
     "help": "SELECT … ON COLUMNS, … ON ROWS FROM [Cube] WHERE (…). Read mode: MDX."},
    {"key": "cube", "label": "Cube", "type": "string", "help": "Read mode: cube view."},
    {"key": "view", "label": "View", "type": "string", "help": "Read mode: cube view."},
    {"key": "private", "label": "Private view", "type": "boolean"},
)

LOAD_PARAMS = (
    {"key": "connection", "label": "Connection", "type": "connection", "dialects": ["tm1"], "required": True},
    {"key": "cube", "label": "Cube", "type": "string", "required": True},
    {"key": "mode", "label": "Mode", "type": "select", "default": "replace_slice", "required": True,
     "options": [
         {"value": "replace_slice", "label": "Replace slice — zero what the data covers, then write (safe to re-run)"},
         {"value": "add", "label": "Add — increment the existing values"},
         {"value": "overwrite", "label": "Overwrite cells — write only these cells"}]},
    {"key": "value_column", "label": "Value column", "type": "column", "default": "value"},
    {"key": "mapping", "label": "Column → dimension mapping (optional)", "type": "text",
     "help": "One per line: column = Dimension. Unmapped dimensions use the column of the same name."},
    {"key": "unknown_elements", "label": "Unknown elements", "type": "select", "default": "fail",
     "options": [{"value": "fail", "label": "Fail and list them"},
                 {"value": "skip", "label": "Skip those rows and report them"}]},
    {"key": "bulk", "label": "Bulk mode (blob upload, for large volumes)", "type": "boolean",
     "help": "Runs through TurboIntegrator, so the TM1 user must be an admin. Replace slice zeroes the same way when it can."},
    {"key": "dry_run", "label": "Dry run — check every element exists, write nothing", "type": "boolean"},
)


def _connect(params):
    fields = _API.get_connection(params.get("connection"), "tm1")
    return connection.connect(fields)


def run_read(ec):
    from .cubes import read
    p = ec.params
    with _connect(p) as tm1:
        return read(tm1, mode=p.get("mode") or "mdx", mdx=p.get("mdx") or "", cube=p.get("cube") or "",
                    view=p.get("view") or "", private=bool(p.get("private")))


def run_load(ec):
    from .cubes import load
    p = ec.params
    with _connect(p) as tm1:
        return load(tm1, ec.df_in, cube=p.get("cube") or "", mapping=p.get("mapping") or "",
                    value_column=p.get("value_column") or "value", mode=p.get("mode") or "replace_slice",
                    bulk=bool(p.get("bulk")), unknown_elements=p.get("unknown_elements") or "fail",
                    dry_run=bool(p.get("dry_run")))


def register(api):
    global _API
    _API = api
    if hasattr(api, "register_connection_type"):
        api.register_connection_type(
            dialect="tm1", label="IBM Planning Analytics (TM1)", fields=connection.FIELDS, test=connection.test,
            description="Planning Analytics v11 on premises, or v12 (Engine, as a Service, IBM Cloud).")
    api.register_engine(api.EngineSpec(
        name="tm1_read", label="TM1 read", description="MDX or a cube view · one row per cell",
        run=run_read, input_mode="none", output_mode="pandas", language="sql", tier="free",
        requires=("TM1py",), params=READ_PARAMS,
        hint="-- Fill the fields above. Output: a column per dimension, then value."))
    api.register_engine(api.EngineSpec(
        name="tm1_load", label="TM1 load", description="Write the frame into a cube · replace, add, overwrite",
        run=run_load, input_mode="pandas", output_mode="pandas", language="python", tier="free",
        requires=("TM1py",), params=LOAD_PARAMS,
        hint="# Fill the fields above. Input: a column per dimension and a value column.\n"
             "# Output: one status row (cells written, skipped, duration)."))
    if hasattr(api, "register_example"):
        from .examples import EXAMPLES
        for name, description, flow in EXAMPLES:
            api.register_example(name=name, description=description, flow=flow)
