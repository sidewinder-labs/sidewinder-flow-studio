"""Refresh a Power BI semantic model with the enhanced refresh API.

1. Check for a refresh already running. Power BI runs one, queues one and
   drops the rest, so firing blind loses requests: wait for it (default) or fail.
2. Start the refresh: type, commit mode, parallelism, retries, and optionally
   the tables and partitions to refresh.
3. Poll until it finishes or the timeout passes (then cancel it, by default).
4. Return a status frame, one row per refreshed object (one row when the
   whole model is refreshed), with a `status` column a Router can branch on.

Enhanced refresh needs the workspace on Premium, Premium Per User, Fabric or
Embedded capacity.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .client import PowerBIError

TYPES = ("full", "dataOnly", "calculate", "clearValues", "automatic")
COMMIT_MODES = ("transactional", "partialBatch")
RUNNING = {"Unknown", "NotStarted", "InProgress"}       # "Unknown" = in progress, per the API


def parse_objects(text: str) -> list[dict]:
    """'Table' or 'Table/Partition' lines → enhanced refresh objects."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        table, _, partition = line.partition("/")
        obj = {"table": table.strip()}
        if partition.strip():
            obj["partition"] = partition.strip()
        out.append(obj)
    return out


def objects_from_frame(df, table_column: str, partition_column: str = "") -> list[dict]:
    """Distinct (table, partition) pairs from upstream columns, e.g. the months just loaded."""
    if df is None:
        raise PowerBIError("Objects from upstream columns: connect the node that holds them.")
    for c in filter(None, (table_column, partition_column)):
        if c not in df.columns:
            raise PowerBIError(f"No column '{c}' upstream. Columns: {', '.join(map(str, df.columns))}")
    cols = [table_column] + ([partition_column] if partition_column else [])
    pairs = df[cols].dropna(subset=[table_column]).drop_duplicates()
    out = []
    for row in pairs.itertuples(index=False):
        obj = {"table": str(row[0])}
        if partition_column and row[1] is not None and str(row[1]) not in ("", "nan", "None"):
            obj["partition"] = str(row[1])
        out.append(obj)
    return out


def request_body(*, refresh_type="full", commit_mode="transactional", max_parallelism=None,
                 retry_count=0, objects=None, timeout_minutes=None) -> dict:
    if refresh_type not in TYPES:
        raise PowerBIError(f"Refresh type '{refresh_type}' isn't one of {', '.join(TYPES)}")
    if commit_mode not in COMMIT_MODES:
        raise PowerBIError(f"Commit mode '{commit_mode}' isn't one of {', '.join(COMMIT_MODES)}")
    body = {"type": refresh_type, "commitMode": commit_mode, "retryCount": int(retry_count or 0),
            "notifyOption": "NoNotification"}
    if max_parallelism:
        body["maxParallelism"] = int(max_parallelism)
    if objects:
        body["objects"] = objects
    if timeout_minutes:
        m = int(timeout_minutes)
        body["timeout"] = f"{m // 60:02d}:{m % 60:02d}:00"
    return body


def _running(r: dict) -> bool:
    return (r.get("extendedStatus") or r.get("status")) in RUNNING


def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def status_frame(refresh_id, detail: dict, objects: list[dict], *, model: str, workspace: str):
    """One row per object (or one for the whole model): the summary columns
    repeated, the object's own status, and the refresh's messages."""
    import pandas as pd
    status = detail.get("extendedStatus") or detail.get("status") or "Unknown"
    start, end = _ts(detail.get("startTime")), _ts(detail.get("endTime"))
    messages = "; ".join(m.get("message", "") for m in detail.get("messages") or [] if m.get("message"))
    per = {(o.get("table"), o.get("partition")): o for o in detail.get("objects") or []}
    rows = []
    for o in objects or [{}]:
        got = per.get((o.get("table"), o.get("partition")), {})
        rows.append({"workspace": workspace, "model": model, "refresh_id": refresh_id, "status": status,
                     "table": o.get("table"), "partition": o.get("partition"),
                     "object_status": got.get("status"), "start": start, "end": end,
                     "duration_s": (end - start).total_seconds() if start and end else None,
                     "message": messages})
    return pd.DataFrame(rows)


def run(client, *, workspace: str, model: str, refresh_type="full", objects=None, commit_mode="transactional",
        max_parallelism=None, retry_count=0, wait=True, timeout_minutes=60, if_busy="wait",
        on_timeout="cancel", poll_seconds=10.0, sleep=None, clock=None):
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    ws = client.workspace(workspace)
    if not ws.get("isOnDedicatedCapacity", False):
        raise PowerBIError(f"Workspace '{ws.get('name')}' isn't on Premium, Premium Per User, Fabric or Embedded "
                           "capacity. Enhanced refresh (tables, partitions, cancellation) needs one of them.")
    ds = client.dataset(ws["id"], model)
    names = {"workspace": ws.get("name", workspace), "model": ds.get("name", model)}
    deadline = clock() + float(timeout_minutes or 60) * 60

    # 1. A refresh already running? Power BI would queue one and drop the rest.
    while True:
        latest = client.refreshes(ws["id"], ds["id"], top=1)
        if not latest or not _running(latest[0]):
            break
        if if_busy == "fail":
            raise PowerBIError(f"A refresh of '{names['model']}' is already in progress "
                               f"(started {latest[0].get('startTime', '?')}).")
        if clock() > deadline:
            raise PowerBIError(f"Timed out waiting for the refresh already running on '{names['model']}' to finish.")
        sleep(poll_seconds)

    # 2. Start
    body = request_body(refresh_type=refresh_type, commit_mode=commit_mode, max_parallelism=max_parallelism,
                        retry_count=retry_count, objects=objects,
                        # the service stops it at the same time, unless it should be left running
                        timeout_minutes=timeout_minutes if (wait and on_timeout == "cancel") else None)
    rid = client.start_refresh(ws["id"], ds["id"], body)
    started = datetime.now(timezone.utc).isoformat()
    if not wait:
        return status_frame(rid, {"status": "Started", "startTime": started}, objects, **names)

    # 3. Poll
    while True:
        detail = client.refresh(ws["id"], ds["id"], rid)
        if not _running(detail):
            return status_frame(rid, detail, objects, **names)
        if clock() > deadline:
            if on_timeout == "cancel":
                client.cancel(ws["id"], ds["id"], rid)
            detail = {**detail, "extendedStatus": "TimedOut",
                      "messages": [{"message": f"Still running after {timeout_minutes} min; "
                                               + ("cancelled." if on_timeout == "cancel" else "left running.")}]}
            return status_frame(rid, detail, objects, **names)
        sleep(poll_seconds)
