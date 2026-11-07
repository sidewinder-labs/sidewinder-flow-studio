"""TM1 plugin with TM1py replaced by a fake server: connection modes, read,
the load modes (replace slice, add, overwrite), dry run, unknown elements,
mapping, and a flow run through the SFS executor."""
import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest

from sfs_plugin_tm1 import connection, cubes, plugin

DIMS = ["Version", "Region", "Month", "Sales Measure"]
ELEMENTS = {"Version": ["Actual", "Budget"], "Region": ["North", "South", "North America"],
            "Month": ["2026-01", "2026-02", "2026-03"], "Sales Measure": ["units", "revenue"]}


class FakeTM1:
    def __init__(self):
        self.cleared, self.written = [], []
        self.cubes = SimpleNamespace(get_dimension_names=lambda cube: list(DIMS))
        self.elements = SimpleNamespace(get_element_names=lambda d, h: list(ELEMENTS[d]))
        self.cells = SimpleNamespace(
            clear_with_mdx=self._clear,
            write_dataframe=lambda cube, data, **kw: self.written.append((cube, data.copy(), kw)),
            execute_mdx_dataframe=lambda mdx: pd.DataFrame({"Region": ["North"], "Month": ["2026-01"], "Value": [5.0]}),
            execute_view_dataframe=lambda **kw: pd.DataFrame({"Region": ["South"], "Value": [7.0]}))

    admin = True

    def _clear(self, cube, mdx):
        if not self.admin:
            NotAdmin = type("TM1pyNotAdminException", (Exception,), {})
            raise NotAdmin("not admin")
        self.cleared.append((cube, mdx))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def frame(**over):
    df = pd.DataFrame({"version": ["Actual"] * 4, "Region": ["North", "North", "South", "South"],
                       "Month": ["2026-01", "2026-02", "2026-01", "2026-02"],
                       "Sales Measure": ["units"] * 4, "value": [1.0, 2.0, 3.0, 4.0]})
    for k, v in over.items():
        df[k] = v
    return df


# ── connection modes ─────────────────────────────────────────────────────────

def test_service_kwargs_per_auth_mode():
    base = {"address": "tm1.local", "port": 8010, "ssl": True, "user": "admin", "password": "pw", "verify_ssl": True}
    kw = connection.service_kwargs({**base, "auth": "basic"})
    assert kw["address"] == "tm1.local" and kw["port"] == 8010 and kw["user"] == "admin" and kw["password"] == "pw"
    assert "namespace" not in kw and "integrated_login" not in kw
    assert connection.service_kwargs({**base, "auth": "cam", "namespace": "LDAP"})["namespace"] == "LDAP"
    assert connection.service_kwargs({**base, "auth": "wia"})["integrated_login"] is True
    paas = connection.service_kwargs({"auth": "paas", "base_url": "https://eu.planninganalytics.saas.ibm.com/api/T/v0/tm1/DB/",
                                      "api_key": "K"})
    assert paas["user"] == "apikey" and paas["password"] == "K" and paas["async_requests_mode"] and "address" not in paas
    cloud = connection.service_kwargs({"auth": "ibm_cloud", "pa_url": "https://pa", "tenant": "T", "database": "D", "api_key": "K"})
    assert cloud["iam_url"] == "https://iam.cloud.ibm.com" and cloud["tenant"] == "T"
    s2s = connection.service_kwargs({"auth": "s2s", "address": "pae", "instance": "i", "database": "d",
                                     "client_id": "c", "client_secret": "s", "user": "u"})
    assert s2s["application_client_id"] == "c" and s2s["application_client_secret"] == "s" and s2s["instance"] == "i"
    with pytest.raises(ValueError, match="needs: CAM namespace"):
        connection.service_kwargs({**base, "auth": "cam"})


def test_every_mode_shows_the_fields_it_requires():
    shown = lambda mode: {f["key"] for f in connection.FIELDS if mode in f.get("show_when", {}).get("auth", [mode])}  # noqa: E731
    for mode, req in connection._REQUIRED.items():
        assert set(req) <= shown(mode), mode


# ── read ─────────────────────────────────────────────────────────────────────

def test_read_mdx_and_view():
    tm1 = FakeTM1()
    assert list(cubes.read(tm1, mode="mdx", mdx="SELECT …")) == ["Region", "Month", "value"]
    assert cubes.read(tm1, mode="view", cube="Sales", view="Default")["value"].tolist() == [7.0]
    with pytest.raises(ValueError, match="write an MDX"):
        cubes.read(tm1, mode="mdx", mdx=" ")


# ── load ─────────────────────────────────────────────────────────────────────

def test_replace_slice_zeroes_exactly_the_covered_slice_then_writes():
    tm1 = FakeTM1()
    st = cubes.load(tm1, frame(), cube="Sales")
    (cube, mdx), = tm1.cleared
    assert cube == "Sales" and mdx == (
        "SELECT NON EMPTY {[Version].[Version].[Actual]} * "
        "{[Region].[Region].[North],[Region].[Region].[South]} * "
        "{[Month].[Month].[2026-01],[Month].[Month].[2026-02]} * "
        "{[Sales Measure].[Sales Measure].[units]} ON 0 FROM [Sales]")
    (_, data, kw), = tm1.written
    assert list(data.columns) == DIMS + ["Value"] and kw["increment"] is False and kw["dimensions"] == DIMS
    row = st.iloc[0]
    assert row["cells_written"] == 4 and row["cells_skipped"] == 0 and row["status"] == "ok" and row["mode"] == "replace_slice"
    assert set(st.columns) == {"cube", "mode", "dry_run", "cells_written", "cells_skipped", "duration_s", "status", "message"}


def test_replace_slice_without_admin_rights_writes_zeros():
    tm1 = FakeTM1()
    tm1.admin = False
    tm1.cells.execute_mdx_dataframe = lambda mdx: pd.DataFrame({
        "Version": ["Actual"], "Region": ["North"], "Month": ["2026-03"], "Sales Measure": ["units"], "Value": [8.0]})
    cubes.load(tm1, frame(), cube="Sales")
    zeros, data = tm1.written
    assert zeros[1]["Value"].tolist() == [0] and zeros[2]["increment"] is False
    assert data[1]["Value"].tolist() == [1.0, 2.0, 3.0, 4.0]


def test_element_names_with_brackets_are_escaped():
    assert cubes._member("Product", "Kit [Pro]") == "[Product].[Product].[Kit [Pro]]]"


def test_add_increments_and_overwrite_never_clears():
    tm1 = FakeTM1()
    cubes.load(tm1, frame(), cube="Sales", mode="add")
    cubes.load(tm1, frame(), cube="Sales", mode="overwrite")
    assert tm1.cleared == [] and [w[2]["increment"] for w in tm1.written] == [True, False]


def test_bulk_mode_uses_blob_upload():
    tm1 = FakeTM1()
    cubes.load(tm1, frame(), cube="Sales", mode="overwrite", bulk=True)
    assert tm1.written[0][2]["use_blob"] is True


def test_dry_run_reports_missing_elements_and_writes_nothing():
    tm1 = FakeTM1()
    df = frame(Region=["North", "Narnia", "South", "Atlantis"])
    st = cubes.load(tm1, df, cube="Sales", dry_run=True).iloc[0]
    assert tm1.cleared == [] and tm1.written == []
    assert st["status"] == "dry run" and st["cells_skipped"] == 2 and st["cells_written"] == 2
    assert "Region: Atlantis, Narnia" in st["message"]
    ok = cubes.load(tm1, frame(), cube="Sales", dry_run=True).iloc[0]
    assert ok["cells_written"] == 4 and "every element exists" in ok["message"] and tm1.written == []


def test_unknown_elements_fail_by_default_or_are_skipped():
    tm1 = FakeTM1()
    df = frame(Region=["North", "Narnia", "South", "South"])
    with pytest.raises(ValueError, match="Region: Narnia"):
        cubes.load(tm1, df, cube="Sales")
    assert tm1.written == []
    st = cubes.load(tm1, df, cube="Sales", unknown_elements="skip").iloc[0]
    assert st["cells_written"] == 3 and st["cells_skipped"] == 1 and "Narnia" in st["message"]
    assert "Narnia" not in tm1.cleared[0][1]


def test_element_names_compare_case_and_space_insensitively():
    tm1 = FakeTM1()
    st = cubes.load(tm1, frame(Region=["north", "NORTH", "northamerica", "South"]), cube="Sales").iloc[0]
    assert st["cells_written"] == 4


def test_mapping_and_missing_dimension_messages():
    tm1 = FakeTM1()
    df = frame().rename(columns={"Region": "area"})
    with pytest.raises(ValueError, match="no column for dimension.s. Region"):
        cubes.load(tm1, df, cube="Sales")
    cubes.load(tm1, df, cube="Sales", mapping="area = Region  # where we sell\n")
    assert tm1.written and list(tm1.written[0][1]["Region"]) == ["North", "North", "South", "South"]
    with pytest.raises(ValueError, match="not dimensions of this cube"):
        cubes.load(tm1, df, cube="Sales", mapping="area = Territory")
    with pytest.raises(ValueError, match="no value column"):
        cubes.load(tm1, frame(), cube="Sales", value_column="amount")


def test_rows_without_a_value_are_not_written():
    tm1 = FakeTM1()
    st = cubes.load(tm1, frame(value=[1.0, None, 3.0, None]), cube="Sales").iloc[0]
    assert st["cells_written"] == 2


# ── inside SFS ───────────────────────────────────────────────────────────────

def test_registered_with_its_connection_type():
    registry = pytest.importorskip("app.engine.registry", reason="needs the SFS backend installed")
    registry.load_plugins()
    loaded = {p["name"]: p for p in registry.loaded_plugins()}
    assert loaded["tm1"]["loaded"], loaded["tm1"]["error"]
    assert registry.connection_type("tm1")["label"] == "IBM Planning Analytics (TM1)"
    assert registry.secret_fields("tm1") == {"password", "api_key", "client_secret"}
    eng = {e["name"]: e for e in registry.list_engines()}
    assert eng["tm1_load"]["params"][0]["dialects"] == ["tm1"] and eng["tm1_read"]["available"]


def test_load_node_in_a_flow(tmp_path, monkeypatch):
    pytest.importorskip("app.engine.registry", reason="needs the SFS backend installed")
    from app.core.config import settings
    from app.core.frame_cache import load_frame
    from app.engine import executor, registry
    monkeypatch.setattr(settings, "FRAME_CACHE_DIR", str(tmp_path / "frames"))

    async def broadcast(*a):
        pass
    monkeypatch.setattr(executor.ws_manager, "broadcast", broadcast)
    registry.load_plugins()
    fake = FakeTM1()
    asked = []
    monkeypatch.setattr(plugin._API, "get_connection", lambda name, dialect=None: asked.append((name, dialect)) or {"name": name})
    monkeypatch.setattr(connection, "connect", lambda fields: fake)
    code = ("df = pd.DataFrame({'Version': ['Actual'], 'Region': ['North'], 'Month': ['2026-01'],"
            " 'Sales Measure': ['units'], 'value': [9.0]})")
    flow = {"id": "f", "nodes": [
        {"id": "s", "type": "trigger", "data": {"label": "Start"}},
        {"id": "d", "type": "processor", "data": {"label": "Data", "engine": "pandas", "code": code}},
        {"id": "l", "type": "processor", "data": {"label": "Load", "engine": "tm1_load", "code": "",
                                                    "connection": "plan", "cube": "Sales"}}],
        "edges": [{"id": "e1", "source": "s", "target": "d"}, {"id": "e2", "source": "d", "target": "l"}]}
    res = {r["node_id"]: r for r in asyncio.run(executor.execute_flow("p", flow, {}, {}))}
    assert res["l"]["status"] == "ok", res["l"]
    assert asked == [("plan", "tm1")] and len(fake.written) == 1
    assert load_frame("p", "l")["cells_written"].tolist() == [1]
