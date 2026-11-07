"""SFS plugin: Power BI semantic model refresh.

Registers the `powerbi` connection type (an Entra ID service principal) and
the `powerbi_refresh` engine. Engine names are saved into flows: never rename.
"""
from __future__ import annotations

from . import client as pbi

_API = None

PARAMS = (
    {"key": "connection", "label": "Connection", "type": "connection", "dialects": ["powerbi"], "required": True},
    {"key": "workspace", "label": "Workspace", "type": "string", "required": True, "help": "Name or id."},
    {"key": "model", "label": "Semantic model", "type": "string", "required": True, "help": "Name or id."},
    {"key": "refresh_type", "label": "Refresh type", "type": "select", "default": "full", "required": True,
     "options": [{"value": "full", "label": "Full — reload data and recalculate"},
                 {"value": "dataOnly", "label": "Data only — reload, no recalculation"},
                 {"value": "calculate", "label": "Calculate — recalculate only"},
                 {"value": "clearValues", "label": "Clear values"},
                 {"value": "automatic", "label": "Automatic — what each object needs"}]},
    {"key": "objects", "label": "Tables and partitions (optional)", "type": "text",
     "help": "One per line: Table, or Table/Partition. Empty refreshes the whole model."},
    {"key": "table_column", "label": "…or tables from an upstream column", "type": "column",
     "help": "e.g. the partitions for the months just loaded. Overrides the list above."},
    {"key": "partition_column", "label": "…and partitions from an upstream column", "type": "column"},
    {"key": "commit_mode", "label": "Commit mode", "type": "select", "default": "transactional",
     "options": [{"value": "transactional", "label": "Transactional — all or nothing"},
                 {"value": "partialBatch", "label": "Partial batch — commit as objects finish"}]},
    {"key": "max_parallelism", "label": "Max parallelism", "type": "number", "help": "Default: Power BI's (10)."},
    {"key": "retry_count", "label": "Retries inside Power BI", "type": "number", "default": 0},
    {"key": "if_busy", "label": "If a refresh is already running", "type": "select", "default": "wait",
     "options": [{"value": "wait", "label": "Wait for it, then refresh"}, {"value": "fail", "label": "Fail"}]},
    {"key": "wait", "label": "Wait for the refresh to finish", "type": "boolean", "default": True},
    {"key": "timeout_minutes", "label": "Timeout (minutes)", "type": "number", "default": 60},
    {"key": "on_timeout", "label": "On timeout", "type": "select", "default": "cancel",
     "options": [{"value": "cancel", "label": "Cancel the refresh"}, {"value": "leave", "label": "Leave it running"}]},
)


def run_refresh(ec):
    from .refresh import objects_from_frame, parse_objects, run
    p = ec.params
    fields = _API.get_connection(p.get("connection"), "powerbi")
    if p.get("table_column"):
        objects = objects_from_frame(ec.df_in, p["table_column"], p.get("partition_column") or "")
    else:
        objects = parse_objects(p.get("objects") or "")
    return run(pbi.Client(fields), workspace=p.get("workspace") or "", model=p.get("model") or "",
               refresh_type=p.get("refresh_type") or "full", objects=objects,
               commit_mode=p.get("commit_mode") or "transactional", max_parallelism=p.get("max_parallelism"),
               retry_count=p.get("retry_count") or 0, wait=p.get("wait", True) is not False,
               timeout_minutes=p.get("timeout_minutes") or 60, if_busy=p.get("if_busy") or "wait",
               on_timeout=p.get("on_timeout") or "cancel")


def register(api):
    global _API
    _API = api
    if hasattr(api, "register_connection_type"):
        api.register_connection_type(
            dialect="powerbi", label="Power BI (service principal)", fields=pbi.FIELDS, test=pbi.test,
            description="An Entra ID app registration allowed to use the Power BI APIs and a member of the "
                        "workspaces it refreshes.")
    api.register_engine(api.EngineSpec(
        name="powerbi_refresh", label="Power BI refresh",
        description="Refresh a semantic model · tables, partitions · waits and reports",
        run=run_refresh, input_mode="pandas", output_mode="pandas", language="python", tier="free",
        requires=("msal", "httpx"), params=PARAMS,
        hint="# Fill the fields above. Output: one row per refreshed object with a status column\n"
             "# (Completed, Failed, Cancelled, TimedOut) — connect a Router to branch on it."))
