"""Power BI refresh against a simulated REST API (httpx MockTransport):
name resolution, capacity check, waiting for a refresh already running,
request bodies, objects from upstream columns, polling, timeout and the
status frame."""
import asyncio
import json

import httpx
import pandas as pd
import pytest

from sfs_plugin_powerbi import client as pbi
from sfs_plugin_powerbi import plugin, refresh

WS = "11111111-2222-3333-4444-555555555555"
DS = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FIELDS = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}


class FakePBI:
    """Workspaces, models and a refresh queue, enough for the refresh flow."""

    def __init__(self, *, dedicated=True, busy_polls=0, run_polls=1, final="Completed"):
        self.dedicated, self.busy_polls, self.run_polls, self.final = dedicated, busy_polls, run_polls, final
        self.posted, self.cancelled, self.calls = [], [], []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, q = req.url.path.removeprefix("/v1.0/myorg"), dict(req.url.params)
        self.calls.append((req.method, path))
        assert req.headers["Authorization"] == "Bearer TOKEN"
        if path == "/groups":
            f = q.get("$filter", "")
            ok = f in (f"name eq 'Finance'", f"id eq '{WS}'")
            return httpx.Response(200, json={"value": [{"id": WS, "name": "Finance",
                                                        "isOnDedicatedCapacity": self.dedicated}] if ok else []})
        if path == f"/groups/{WS}/datasets":
            return httpx.Response(200, json={"value": [{"id": DS, "name": "Sales"}, {"id": "x", "name": "HR"}]})
        if path == f"/groups/{WS}/datasets/{DS}/refreshes" and req.method == "GET":
            if self.busy_polls > 0:
                self.busy_polls -= 1
                return httpx.Response(200, json={"value": [{"status": "Unknown", "startTime": "2026-11-07T08:00:00Z"}]})
            return httpx.Response(200, json={"value": [{"status": "Completed"}]})
        if path == f"/groups/{WS}/datasets/{DS}/refreshes" and req.method == "POST":
            self.posted.append(json.loads(req.content))
            return httpx.Response(202, headers={"Location": f"https://api.powerbi.com/v1.0/myorg/groups/{WS}/datasets/{DS}/refreshes/r-42"})
        if path == f"/groups/{WS}/datasets/{DS}/refreshes/r-42" and req.method == "GET":
            if self.run_polls > 0:
                self.run_polls -= 1
                return httpx.Response(200, json={"extendedStatus": "InProgress", "startTime": "2026-11-07T09:00:00Z"})
            objs = [{"table": o["table"], "partition": o.get("partition"), "status": self.final}
                    for o in (self.posted[-1].get("objects") or [])]
            return httpx.Response(200, json={"extendedStatus": self.final, "startTime": "2026-11-07T09:00:00Z",
                                             "endTime": "2026-11-07T09:02:30Z", "objects": objs,
                                             "messages": [{"message": "done"}] if self.final == "Completed" else
                                             [{"message": "Column 'x' not found"}]})
        if path == f"/groups/{WS}/datasets/{DS}/refreshes/r-42" and req.method == "DELETE":
            self.cancelled.append("r-42")
            return httpx.Response(200)
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": path}})


def client(fake):
    return pbi.Client(FIELDS, http=httpx.Client(transport=httpx.MockTransport(fake.handler)), token="TOKEN")


def go(fake, **kw):
    t = {"now": 0.0}
    sleeps = []
    out = refresh.run(client(fake), workspace=kw.pop("workspace", "Finance"), model=kw.pop("model", "Sales"),
                      sleep=lambda s: (sleeps.append(s), t.__setitem__("now", t["now"] + s)),
                      clock=lambda: t["now"], poll_seconds=10, **kw)
    return out, sleeps


def test_full_refresh_resolves_names_and_returns_a_status_frame():
    fake = FakePBI()
    df, _ = go(fake)
    assert fake.posted == [{"type": "full", "commitMode": "transactional", "retryCount": 0,
                            "notifyOption": "NoNotification", "timeout": "01:00:00"}]
    row = df.iloc[0]
    assert len(df) == 1 and row["status"] == "Completed" and row["refresh_id"] == "r-42"
    assert row["workspace"] == "Finance" and row["model"] == "Sales" and row["duration_s"] == 150.0
    assert row["message"] == "done"
    assert list(df.columns) == ["workspace", "model", "refresh_id", "status", "table", "partition",
                                "object_status", "start", "end", "duration_s", "message"]


def test_ids_work_as_well_as_names():
    df, _ = go(FakePBI(), workspace=WS, model=DS)
    assert df.iloc[0]["model"] == "Sales"


def test_a_refresh_already_running_is_waited_for_first():
    fake = FakePBI(busy_polls=2)
    df, sleeps = go(fake)
    gets_before_post = [c for c in fake.calls[:fake.calls.index(("POST", f"/groups/{WS}/datasets/{DS}/refreshes"))]
                        if c[1].endswith("/refreshes")]
    assert len(gets_before_post) == 3 and sleeps[:2] == [10, 10] and df.iloc[0]["status"] == "Completed"


def test_or_fails_when_asked_to():
    with pytest.raises(pbi.PowerBIError, match="already in progress"):
        go(FakePBI(busy_polls=1), if_busy="fail")


def test_tables_and_partitions_from_text_and_from_an_upstream_column():
    assert refresh.parse_objects("Sales/Sales-2026-10\n# skip\n\nCustomers\n") == [
        {"table": "Sales", "partition": "Sales-2026-10"}, {"table": "Customers"}]
    up = pd.DataFrame({"tbl": ["Sales", "Sales", "Sales"], "part": ["Sales-2026-09", "Sales-2026-10", "Sales-2026-10"]})
    objs = refresh.objects_from_frame(up, "tbl", "part")
    assert objs == [{"table": "Sales", "partition": "Sales-2026-09"}, {"table": "Sales", "partition": "Sales-2026-10"}]
    fake = FakePBI()
    df, _ = go(fake, objects=objs, refresh_type="dataOnly", commit_mode="partialBatch", max_parallelism=4, retry_count=2)
    body = fake.posted[0]
    assert body["objects"] == objs and body["type"] == "dataOnly" and body["commitMode"] == "partialBatch"
    assert body["maxParallelism"] == 4 and body["retryCount"] == 2
    assert df["partition"].tolist() == ["Sales-2026-09", "Sales-2026-10"] and set(df["object_status"]) == {"Completed"}
    with pytest.raises(pbi.PowerBIError, match="No column 'month'"):
        refresh.objects_from_frame(up, "month")


def test_failure_is_a_status_not_an_exception():
    df, _ = go(FakePBI(final="Failed"))
    assert df.iloc[0]["status"] == "Failed" and "Column 'x' not found" in df.iloc[0]["message"]


def test_timeout_cancels_or_leaves_running():
    fake = FakePBI(run_polls=99)
    df, _ = go(fake, timeout_minutes=1)
    assert df.iloc[0]["status"] == "TimedOut" and fake.cancelled == ["r-42"] and "cancelled" in df.iloc[0]["message"]
    fake2 = FakePBI(run_polls=99)
    df2, _ = go(fake2, timeout_minutes=1, on_timeout="leave")
    assert fake2.cancelled == [] and "timeout" not in fake2.posted[0] and "left running" in df2.iloc[0]["message"]


def test_not_waiting_returns_at_once():
    fake = FakePBI(run_polls=5)
    df, _ = go(fake, wait=False)
    assert df.iloc[0]["status"] == "Started" and "timeout" not in fake.posted[0]


def test_shared_capacity_fails_with_a_clear_message():
    with pytest.raises(pbi.PowerBIError, match="Premium, Premium Per User"):
        go(FakePBI(dedicated=False))


def test_missing_names_list_what_exists():
    with pytest.raises(pbi.PowerBIError, match="Workspace 'Marketing' not found"):
        go(FakePBI(), workspace="Marketing")
    with pytest.raises(pbi.PowerBIError, match="It has: HR, Sales"):
        go(FakePBI(), model="Ops")


def test_errors_are_explained_and_throttling_retried(monkeypatch):
    monkeypatch.setattr(pbi.time, "sleep", lambda s: None)
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(401, json={"error": {"code": "PowerBINotAuthorizedException"}})
    c = pbi.Client(FIELDS, http=httpx.Client(transport=httpx.MockTransport(h)), token="TOKEN")
    with pytest.raises(pbi.PowerBIError, match="Power BI APIs in the tenant settings"):
        c.workspace("Finance")
    assert n["i"] == 2


def test_request_body_validates():
    with pytest.raises(pbi.PowerBIError, match="Refresh type"):
        refresh.request_body(refresh_type="Full")
    assert refresh.request_body(timeout_minutes=135)["timeout"] == "02:15:00"


def test_registered_and_runs_in_a_flow(tmp_path, monkeypatch):
    registry = pytest.importorskip("app.engine.registry", reason="needs the SFS backend installed")
    from app.core.config import settings
    from app.core.frame_cache import load_frame
    from app.engine import executor
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path / "frames"))

    async def broadcast(*a):
        pass
    monkeypatch.setattr(executor.ws_manager, "broadcast", broadcast)
    registry.load_plugins()
    assert registry.connection_type("powerbi") and registry.secret_fields("powerbi") == {"client_secret"}
    fake = FakePBI()
    monkeypatch.setattr(plugin._API, "get_connection", lambda name, dialect=None: dict(FIELDS, name=name))
    real = client(fake)
    monkeypatch.setattr(plugin.pbi, "Client", lambda fields: real)
    monkeypatch.setattr(refresh.time, "sleep", lambda s: None)
    up = "df = pd.DataFrame({'t': ['Sales'], 'p': ['Sales-2026-10']})"
    flow = {"id": "f", "nodes": [
        {"id": "s", "type": "trigger", "data": {"label": "Start"}},
        {"id": "u", "type": "processor", "data": {"label": "Loaded months", "engine": "pandas", "code": up}},
        {"id": "r", "type": "processor", "data": {"label": "Refresh", "engine": "powerbi_refresh", "code": "",
                                                    "connection": "pbi", "workspace": "Finance", "model": "Sales",
                                                    "table_column": "t", "partition_column": "p"}}],
        "edges": [{"id": "e1", "source": "s", "target": "u"}, {"id": "e2", "source": "u", "target": "r"}]}
    res = {r["node_id"]: r for r in asyncio.run(executor.execute_flow("p", flow, {}, {}))}
    assert res["r"]["status"] == "ok", res["r"]
    out = load_frame("p", "r")
    assert out["status"].tolist() == ["Completed"] and out["partition"].tolist() == ["Sales-2026-10"]
    assert fake.posted[0]["objects"] == [{"table": "Sales", "partition": "Sales-2026-10"}]
