"""Diagnostics: install type, versions, no secrets or paths."""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import diagnostics


@pytest.fixture
def client():
    app = FastAPI(version="9.9")
    app.include_router(diagnostics.router, prefix="/api/diagnostics")
    return TestClient(app)


@pytest.mark.parametrize("env,want,label", [("docker", "docker", "Install: Docker"),
                                            ("desktop", "desktop", "Install: Desktop"),
                                            (None, "source", "Install: Built from source (unsupported)"),
                                            ("whatever", "source", "Install: Built from source (unsupported)")])
def test_install_type(client, monkeypatch, env, want, label):
    if env is None:
        monkeypatch.delenv("SFS_INSTALL", raising=False)
    else:
        monkeypatch.setenv("SFS_INSTALL", env)
    d = client.get("/api/diagnostics/").json()
    assert d["install"] == want and label in d["text"]


def test_contents_and_nothing_sensitive(client, monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "SUPER-SECRET-VALUE")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-NOPE")
    d = client.get("/api/diagnostics/").json()
    assert d["version"] == "9.9" and d["python"].count(".") == 2 and d["arch"]
    assert d["packages"]["pandas"] and d["packages"]["pyarrow"] and d["packages"]["duckdb"]
    assert all({"name", "version", "status"} <= set(p) for p in d["plugins"])
    text = json.dumps(d)
    assert "SUPER-SECRET" not in text and "sk-ant" not in text
    for p in {str(Path.home()), str(Path.cwd()), sys.prefix, str(tmp_path)}:
        assert p not in text, p
    assert d["text"].startswith("SFS 9.9\nInstall:")


def test_desktop_launcher_reports_desktop(tmp_path):
    from app.desktop import desktop_environment
    assert desktop_environment(tmp_path, environ={})["SFS_INSTALL"] == "desktop"
