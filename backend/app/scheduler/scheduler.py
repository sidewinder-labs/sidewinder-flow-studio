"""
APScheduler wrapper — scheduled execution.

Per-flow (configured on the schedule trigger node):
  retries + exponential backoff · failure alerting (Slack/email) ·
  run history/audit rows per attempt · calendar rules (weekdays-only, skip dates)
"""
import asyncio
import logging
import time
import uuid
from datetime import datetime, UTC

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone="UTC")


def start_scheduler():
    _scheduler.start()
    log.info("APScheduler started")


def stop_scheduler():
    _scheduler.shutdown(wait=False)


def _trigger_config(flow: dict) -> dict:
    """Read advanced schedule options from the flow's trigger node."""
    for n in flow.get("nodes", []):
        if n.get("type") == "trigger":
            d = n.get("data", {})
            return {
                "retries": int(d.get("retries") or 0),
                "backoff_sec": int(d.get("retry_backoff_sec") or 60),
                "alert_on_failure": bool(d.get("alert_on_failure")),
                "alert_email": d.get("alert_email") or "",
                "weekdays_only": bool(d.get("weekdays_only")),
                "skip_dates": [s.strip() for s in (d.get("skip_dates") or "").split(",") if s.strip()],
            }
    return {"retries": 0, "backoff_sec": 60, "alert_on_failure": False,
            "alert_email": "", "weekdays_only": False, "skip_dates": []}


async def _record(status: str, run_id: str, project_id: str, flow_id: str,
                  flow_name: str | None, attempt: int, t0: float | None = None,
                  results: list | None = None, err: str | None = None):
    from app.core.database import AsyncSessionLocal
    from app.models.run_history import RunHistory
    async with AsyncSessionLocal() as db:
        if status == "running":
            db.add(RunHistory(id=run_id, project_id=project_id, flow_id=flow_id,
                              flow_name=flow_name, trigger="schedule",
                              status="running", attempt=attempt))
        else:
            row = await db.get(RunHistory, run_id)
            if row:
                results = results or []
                row.status = status
                row.finished_at = datetime.now(UTC)
                row.duration_ms = round((time.perf_counter() - t0) * 1000) if t0 else None
                row.nodes_ok = sum(1 for r in results if r.get("status") == "ok")
                row.nodes_error = sum(1 for r in results if r.get("status") == "error")
                row.error = err
        await db.commit()


async def _run_scheduled(project_id: str, flow_id: str):
    """One scheduled firing: calendar check → attempt loop with backoff →
    history per attempt → alert on final failure."""
    from app.services.project_service import get_project
    from app.services.connection_service import build_url, get_storage_options, get_connect_args
    from app.core.database import AsyncSessionLocal
    from app.models.connection import Connection
    from app.engine.executor import execute_flow
    from app.services import alert_service
    from sqlalchemy import select

    project = get_project(project_id)
    flow = next((f for f in (project or {}).get("flows", []) if f.get("id") == flow_id), None)
    if not flow:
        # Deletion unregisters schedules; this only happens if the project
        # file was removed by hand. Stop firing rather than fail every time.
        log.error(f"Scheduled flow {flow_id} not found in project {project_id}; unscheduling it")
        unregister_flow(flow_id)
        return
    cfg = _trigger_config(flow)
    flow_name = flow.get("name")

    # ── Calendar rules ────────────────────────────────────────────────────
    now = datetime.now(UTC)
    if cfg["weekdays_only"] and now.weekday() >= 5:
        log.info(f"[{flow_name}] skipped — weekend (weekdays_only)")
        return
    if now.strftime("%Y-%m-%d") in cfg["skip_dates"]:
        log.info(f"[{flow_name}] skipped — {now:%Y-%m-%d} in skip_dates")
        return

    async with AsyncSessionLocal() as db:
        conns = (await db.execute(select(Connection))).scalars().all()
        from app.services.connection_service import connection_maps
        _m = connection_maps(conns)
        urls, dialects = _m["urls"], _m["dialects"]
        storage, cargs = _m["storage"], _m["cargs"]

    max_attempts = cfg["retries"] + 1
    for attempt in range(1, max_attempts + 1):
        run_id = str(uuid.uuid4())
        await _record("running", run_id, project_id, flow_id, flow_name, attempt)
        t0 = time.perf_counter()
        err = None
        results: list = []
        try:
            results = await execute_flow(
                project_id=project_id, flow=flow, connection_urls=urls,
                variables={}, storage_options=storage, connection_dialects=dialects,
                connection_cargs=cargs,
            )
        except Exception as e:
            err = str(e)

        n_err = sum(1 for r in results if r.get("status") == "error")
        if err or n_err:
            detail = err or next((r.get("detail") for r in results if r.get("status") == "error"), "node error")
            await _record("error", run_id, project_id, flow_id, flow_name, attempt, t0, results, detail)
            if attempt < max_attempts:
                wait = cfg["backoff_sec"] * (2 ** (attempt - 1))   # exponential backoff
                log.warning(f"[{flow_name}] attempt {attempt} failed — retrying in {wait}s")
                await asyncio.sleep(wait)
                continue
            log.error(f"[{flow_name}] failed after {max_attempts} attempt(s)")
            if cfg["alert_on_failure"]:
                alert_service.alert_failure(flow_name or flow_id, project_id,
                                            attempt, max_attempts, detail, cfg["alert_email"])
            return
        status = "stopped" if any(r.get("status") == "stopped" for r in results) else "ok"
        await _record(status, run_id, project_id, flow_id, flow_name, attempt, t0, results)
        return


def _job(project_id: str, flow_id: str):
    asyncio.run(_run_scheduled(project_id, flow_id))


def register_flow(flow_id: str, project_id: str, cron: str, timezone: str = "UTC"):
    job_id = f"flow:{flow_id}"
    try:
        trigger = CronTrigger.from_crontab(cron, timezone=timezone)
    except Exception as e:
        log.error(f"Invalid cron '{cron}': {e}")
        return
    _scheduler.add_job(_job, trigger=trigger, id=job_id,
                       args=[project_id, flow_id], replace_existing=True,
                       misfire_grace_time=60)
    log.info(f"Registered schedule for flow {flow_id}: {cron} ({timezone})")


def unregister_flow(flow_id: str):
    if _scheduler.get_job(f"flow:{flow_id}"):
        _scheduler.remove_job(f"flow:{flow_id}")


def _schedule_of(flow: dict) -> tuple[str, str] | None:
    """(cron, timezone) when the flow is active and its trigger is a schedule."""
    trigger = next((n for n in flow.get("nodes", []) if n.get("type") == "trigger"), None)
    if not trigger or not flow.get("active"):
        return None
    td = trigger.get("data", {})
    if td.get("run_mode") == "schedule" and td.get("cron"):
        return td["cron"], td.get("timezone", "UTC")
    return None


def sync_schedules(project_id: str, flows: list[dict], removed_flow_ids=()) -> None:
    """Make the scheduler match a project's flows: register active scheduled
    flows, unregister the rest and any flows that were removed."""
    for flow in flows:
        sched = _schedule_of(flow)
        if sched:
            register_flow(flow["id"], project_id, *sched)
        else:
            unregister_flow(flow["id"])
    for flow_id in removed_flow_ids:
        unregister_flow(flow_id)


def load_all_schedules() -> None:
    """At startup: schedules live only in memory, so register every project's."""
    from app.services.project_service import list_projects, get_project
    for p in list_projects():
        project = get_project(p["id"])
        if project:
            sync_schedules(project["id"], project.get("flows", []))


def scheduled_flow_ids() -> set[str]:
    return {job.id.split(":", 1)[1] for job in _scheduler.get_jobs() if job.id.startswith("flow:")}


def get_next_run(flow_id: str) -> str | None:
    job = _scheduler.get_job(f"flow:{flow_id}")
    nxt = getattr(job, "next_run_time", None)      # a job pending a scheduler start has none yet
    if nxt:
        return nxt.isoformat()
    return None
