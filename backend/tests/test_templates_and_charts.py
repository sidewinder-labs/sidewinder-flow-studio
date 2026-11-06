"""Plugin chart templates (registry contract) and multi-input Chart nodes."""
import pandas as pd
import pytest

from app.engine import registry
from app.engine.executor import render_chart


@pytest.fixture
def clean_templates(monkeypatch):
    monkeypatch.setattr(registry, "TEMPLATES", [])


def test_plugin_registers_a_chart_template(clean_templates):
    api = registry.RegistryAPI(source="plugin:demo")
    api.register_template(kind="chart", name="Demo plot", code="df.plot()", description="d")
    assert registry.list_templates("chart") == [
        {"kind": "chart", "name": "Demo plot", "code": "df.plot()", "description": "d",
         "source": "plugin:demo"}]
    assert registry.list_templates("other") == []


def test_duplicate_template_is_ignored(clean_templates):
    api = registry.RegistryAPI(source="plugin:a")
    api.register_template(kind="chart", name="Same", code="1")
    registry.RegistryAPI(source="plugin:b").register_template(kind="chart", name="Same", code="2")
    assert [t["code"] for t in registry.list_templates()] == ["1"]


def test_unknown_template_kind_is_refused(clean_templates):
    with pytest.raises(ValueError, match="Unknown template kind 'chrat'"):
        registry.RegistryAPI(source="plugin:a").register_template(kind="chrat", name="x", code="")


def test_chart_gets_every_input():
    a, b = pd.DataFrame({"x": [1, 2]}), pd.DataFrame({"y": [3, 4, 5]})
    code = "result = {'n': [len(f) for f in dfs], 'df2': list(df2.columns)}"
    out = render_chart(a, code, {}, [b])
    assert out["chart_type"] == "echarts" and '"n": [2, 3]' in out["payload"] and '"df2": ["y"]' in out["payload"]


def test_single_input_chart_still_has_dfs():
    out = render_chart(pd.DataFrame({"x": [1]}), "result = {'n': len(dfs)}", {})
    assert '"n": 1' in out["payload"]


# ── Examples ──────────────────────────────────────────────────────────────────

EXAMPLE_FLOW = {"name": "Demo", "nodes": [
    {"id": "t", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"label": "Start"}},
    {"id": "p", "type": "processor", "position": {"x": 300, "y": 0}, "data": {"label": "P", "code": "df = pd.DataFrame({'a': [1]})"}}],
    "edges": [{"id": "e", "source": "t", "target": "p"}]}


@pytest.fixture
def clean_examples(monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(registry, "EXAMPLES", [])
    monkeypatch.setattr(settings, "PROJECTS_DIR", str(tmp_path / "projects"))


def test_plugin_registers_an_example(clean_examples):
    registry.RegistryAPI(source="plugin:demo").register_example(name="Demo", description="d", flow=EXAMPLE_FLOW)
    assert registry.list_examples() == [{"name": "Demo", "description": "d", "source": "plugin:demo"}]
    with pytest.raises(ValueError, match="nodes"):
        registry.register_example(name="Bad", flow={"edges": []})


def test_project_from_an_example(clean_examples):
    from fastapi.testclient import TestClient
    from app.core.app_factory import create_app
    from app.services import project_service

    registry.register_example(name="Demo", flow=EXAMPLE_FLOW)
    client = TestClient(create_app())
    r = client.post("/api/projects/from-example", json={"example": "Demo"})
    assert r.status_code == 201
    p = project_service.get_project(r.json()["id"])
    f = p["flows"][0]
    assert p["name"] == "Demo" and f["name"] == "Demo"
    assert sorted(n["id"] for n in f["nodes"]) == ["p", "t"] and len(f["edges"]) == 1   # its own trigger kept
    second = client.post("/api/projects/from-example", json={"example": "Demo", "name": "Mine"}).json()
    assert second["name"] == "Mine" and second["flows"][0]["id"] != f["id"]
    assert client.post("/api/projects/from-example", json={"example": "Nope"}).status_code == 404
    assert client.get("/api/capabilities/").json()["examples"][0]["name"] == "Demo"


def test_example_without_a_trigger_gets_one(clean_examples):
    from app.services import project_service
    flow = {"nodes": [EXAMPLE_FLOW["nodes"][1]], "edges": []}
    f = project_service.create_project_from_flow("X", "", flow)["flows"][0]
    assert [n["type"] for n in f["nodes"]] == ["trigger", "processor"]
