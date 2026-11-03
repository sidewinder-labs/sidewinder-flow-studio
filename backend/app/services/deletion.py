"""Deleting projects and flows without leaving anything running or behind.

A project goes to the trash (project_service). Around that, deletion
unregisters the schedules of the flows that go, removes their cached frames
(regenerable, and often large), and, only when asked, drops their persisted
tables. Run history is kept: it records what ran.

Persisted tables (right-click → Persist, target "local") live in one shared
DuckDB file and are named by the user, so two projects can write to the same
table. A table is dropped only if no flow that stays still persists to it.
Tables persisted to a connected database are the user's and are never dropped.
"""
from __future__ import annotations

from app.core import frame_cache, report_store
from app.engine.active_runs import running_flows
from app.scheduler.scheduler import scheduled_flow_ids, sync_schedules
from app.services import project_service


class Busy(Exception):
    """A flow that would be deleted is running."""


def local_persist_tables(flows: list[dict]) -> set[str]:
    """Names of the local persisted tables these flows write (as the executor names them)."""
    out = set()
    for flow in flows:
        for n in flow.get("nodes", []):
            data = n.get("data") or {}
            conf = data.get("persist") or {}
            if conf.get("enabled") and conf.get("target", "local") == "local":
                name = conf.get("table_name") or data.get("label", n.get("type", ""))
                out.add(report_store.sanitize_table(name))
    return out


def _tables_kept_elsewhere(except_project: str) -> set[str]:
    keep = set()
    for p in project_service.list_projects():
        if p["id"] != except_project:
            project = project_service.get_project(p["id"])
            keep |= local_persist_tables((project or {}).get("flows", []))
    return keep


def _droppable(going: list[dict], staying: list[dict], project_id: str) -> list[str]:
    """Persisted tables of the flows that go, that exist and that nothing else uses."""
    candidates = local_persist_tables(going) - local_persist_tables(staying) - _tables_kept_elsewhere(project_id)
    return sorted(t for t in candidates if report_store.has_persist(t))


def _flow_summary(flows: list[dict]) -> list[dict]:
    scheduled = scheduled_flow_ids()
    return [{"id": f["id"], "name": f.get("name"), "scheduled": f["id"] in scheduled} for f in flows]


def preview(project_id: str, flow_id: str | None = None) -> dict | None:
    """What deleting the project (or one of its flows) would affect, for the dialog."""
    project = project_service.get_project(project_id)
    if project is None:
        return None
    flows = project.get("flows", [])
    going = [f for f in flows if flow_id is None or f["id"] == flow_id]
    staying = [f for f in flows if f not in going]
    live = running_flows(project_id)
    return {
        "flows": _flow_summary(going),
        "persisted_tables": _droppable(going, staying, project_id),
        "running": any(f["id"] in live for f in going),
    }


def _clean_up(project_id: str, going: list[dict], staying: list[dict], tables: list[str],
              whole_project: bool) -> dict:
    sync_schedules(project_id, [], removed_flow_ids=[f["id"] for f in going])
    if whole_project:
        frame_cache.delete_project_cache(project_id)
    else:
        # Node ids can repeat across flows (a copied flow); keep frames still in use.
        still_used = {n["id"] for f in staying for n in f.get("nodes", [])}
        gone = {n["id"] for f in going for n in f.get("nodes", [])} - still_used
        frame_cache.delete_node_frames(project_id, gone)
    for t in tables:
        report_store.drop_persist(t)
    return {"flows": [f["id"] for f in going], "dropped_tables": tables}


def delete_project(project_id: str, drop_persisted: bool = False) -> dict | None:
    """Trash a project; unschedule its flows and delete its frames."""
    project = project_service.get_project(project_id)
    if project is None:
        return None
    if running_flows(project_id):
        raise Busy("This project has a flow running. Stop it, or wait for it to finish, then delete.")
    flows = project.get("flows", [])
    # Decide the tables while the project still counts as live.
    tables = _droppable(flows, [], project_id) if drop_persisted else []
    project_service.delete_project(project_id)
    return _clean_up(project_id, flows, [], tables, whole_project=True)


def removed_flows(project_id: str, incoming_flows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(flows the save removes, flows that stay), comparing with the stored project."""
    stored = (project_service.get_project(project_id) or {}).get("flows", [])
    keep_ids = {f.get("id") for f in incoming_flows}
    return [f for f in stored if f["id"] not in keep_ids], incoming_flows


def check_removal(project_id: str, going: list[dict]) -> None:
    live = running_flows(project_id)
    busy = [f.get("name") or f["id"] for f in going if f["id"] in live]
    if busy:
        raise Busy(f"Flow {', '.join(busy)} is running. Stop it, or wait for it to finish, then delete it.")


def tables_to_drop(project_id: str, going: list[dict], staying: list[dict]) -> list[str]:
    return _droppable(going, staying, project_id)


def remove_flows(project_id: str, going: list[dict], staying: list[dict], tables: list[str]) -> dict:
    """After a save that removed flows: unschedule them and delete their frames
    (and the given persisted tables)."""
    return _clean_up(project_id, going, staying, tables, whole_project=False)
