"""Desktop launcher (app/desktop.py): data-directory handling and the readiness
handshake the Tauri shell relies on."""
import json
import os
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from app import desktop

BACKEND = Path(__file__).resolve().parents[1]
SETTINGS_VARS = ("PROJECTS_DIR", "FRAME_CACHE_DIR", "REPORTS_DIR", "PERSIST_DB", "METADATA_DB_URL",
                 "SECRET_KEY", "SERVE_STATIC", "STATIC_DIR", "DEBUG", "HOST", "SFS_DESKTOP", "SFS_ENV_FILE", "SFS_DATA_DIR", "SFS_INSTALL")


def test_data_dirs_point_into_the_data_dir(tmp_path):
    env = desktop.desktop_environment(tmp_path, environ={})
    assert env["PROJECTS_DIR"] == str(tmp_path.resolve() / "projects")
    assert env["FRAME_CACHE_DIR"] == str(tmp_path.resolve() / "frame_cache")
    assert env["PERSIST_DB"] == str(tmp_path.resolve() / "reports_data" / "persist.duckdb")
    assert env["METADATA_DB_URL"] == "sqlite+aiosqlite:///" + (tmp_path.resolve() / "flow_studio.db").as_posix()
    assert env["HOST"] == "127.0.0.1" and env["SERVE_STATIC"] == "true" and env["SFS_DESKTOP"] == "1"
    assert env["SFS_ENV_FILE"] == str(tmp_path.resolve() / ".env")


def test_existing_settings_win(tmp_path):
    env = desktop.desktop_environment(tmp_path, environ={"PROJECTS_DIR": "/elsewhere", "SECRET_KEY": "k"})
    assert "PROJECTS_DIR" not in env and "SECRET_KEY" not in env
    assert not (tmp_path / desktop.SECRET_KEY_FILE).exists()


def test_secret_key_is_created_once_owner_only(tmp_path):
    from cryptography.fernet import Fernet

    first = desktop.desktop_environment(tmp_path, environ={})["SECRET_KEY"]
    second = desktop.desktop_environment(tmp_path, environ={})["SECRET_KEY"]
    assert first == second
    Fernet(first.encode())                                   # a valid key
    key_file = tmp_path / desktop.SECRET_KEY_FILE
    if os.name == "posix":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_socket_is_local_only():
    sock = desktop.bind_local_socket()
    try:
        host, port = sock.getsockname()
        assert host == "127.0.0.1" and port > 0
    finally:
        sock.close()


def test_ready_line_format():
    assert desktop.ready_line(53817) == "SFS_READY port=53817"


def test_default_static_dir_is_next_to_main_py(monkeypatch, tmp_path):
    """Regression: the default pointed at backend/app/static, so single-container
    mode never served the frontend."""
    from app.core import app_factory

    static = BACKEND / "static"
    assert Path(app_factory.__file__).resolve().parents[2] / "static" == static


@pytest.fixture
def launched(tmp_path):
    """Start the launcher as the shell does and wait for its ready line."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text('<div id="root"></div>')
    env = {k: v for k, v in os.environ.items() if k not in SETTINGS_VARS}
    env["STATIC_DIR"] = str(static)
    env["PYTHONPATH"] = str(BACKEND)
    # Launched from a folder whose .env Settings would reject: it must be ignored
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    (cwd / ".env").write_text("NOT_AN_SFS_SETTING=1\n")
    proc = subprocess.Popen([sys.executable, "-m", "app.desktop", "--data-dir", str(tmp_path / "data")],
                            cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    deadline, line = time.time() + 60, ""
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line or line.startswith(desktop.READY_PREFIX):
            break
    yield proc, line, tmp_path / "data"
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def test_handshake_serves_app_and_api_then_exits_on_stdin_eof(launched):
    proc, line, data = launched
    assert line.startswith("SFS_READY port="), line
    port = int(line.strip().split("port=")[1])

    caps = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/capabilities/", timeout=10))
    assert any(e["name"] == "pandas" for e in caps["engines"])
    assert b'id="root"' in urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read()
    assert b'id="root"' in urllib.request.urlopen(f"http://127.0.0.1:{port}/some/client/route", timeout=10).read()
    with pytest.raises(urllib.error.HTTPError) as unknown_api:     # not the app's index page
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/no-such-endpoint", timeout=10)
    assert unknown_api.value.code == 404
    assert (data / "projects").is_dir() and (data / "frame_cache").is_dir()
    assert (data / desktop.SECRET_KEY_FILE).exists()

    # Not reachable on other interfaces: the socket is bound to 127.0.0.1 only
    with socket.socket() as s:
        s.settimeout(1)
        other = socket.gethostbyname(socket.gethostname())
        if other != "127.0.0.1" and not other.startswith("127."):
            assert s.connect_ex((other, port)) != 0

    proc.stdin.close()                                       # the shell went away
    assert proc.wait(timeout=15) == 0


def test_desktop_hides_reports(launched):
    """Desktop ships no Streamlit viewer, so the UI hides the Report node."""
    proc, line, _ = launched
    port = int(line.strip().split("port=")[1])
    caps = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/capabilities/", timeout=10))
    assert caps["features"]["desktop"] is True and caps["features"]["reports"] is False


def _call(port, path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"" if method != "GET" else None)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read()
            return r.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        return e.code, None


def test_desktop_info_welcome_and_logs(launched):
    proc, line, data = launched
    port = int(line.strip().split("port=")[1])

    status, info = _call(port, "/api/desktop/")
    assert status == 200
    assert info["data_dir"] == str(data.resolve())
    assert info["secret_key"] == str(data.resolve() / desktop.SECRET_KEY_FILE)
    assert info["welcome_seen"] is False and info["logs"] == []

    # first run is over once the welcome screen is closed; it can be shown again
    assert _call(port, "/api/desktop/welcome-seen", "POST")[0] == 204
    assert _call(port, "/api/desktop/")[1]["welcome_seen"] is True
    assert _call(port, "/api/desktop/welcome-seen", "DELETE")[0] == 204
    assert _call(port, "/api/desktop/")[1]["welcome_seen"] is False

    # logs are read by name only, never by path
    (data / "logs").mkdir()
    (data / "logs" / "backend.log").write_bytes(b"x" * 10 + b"last line\n")
    assert [l["name"] for l in _call(port, "/api/desktop/")[1]["logs"]] == ["backend.log"]
    status, log = _call(port, "/api/desktop/logs/backend.log")
    assert status == 200 and log["text"].endswith("last line\n") and not log["truncated"]
    for bad in ("..%2Fsecret.key", "secret.key", "%2Fetc%2Fpasswd"):
        assert _call(port, f"/api/desktop/logs/{bad}")[0] == 404, bad


def test_launch_environment_drops_bundle_paths(monkeypatch):
    from app.api.desktop import _launch_environment

    monkeypatch.setenv("APPDIR", "/tmp/.mount_x")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    monkeypatch.setenv("XDG_DATA_DIRS", os.pathsep.join(["/tmp/.mount_x/usr/share", "/usr/share"]))
    monkeypatch.setenv("GIO_MODULE_DIR", "/tmp/.mount_x/usr/lib/gio")
    env = _launch_environment()
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib" and "LD_LIBRARY_PATH_ORIG" not in env
    assert env["XDG_DATA_DIRS"] == "/usr/share"
    assert "GIO_MODULE_DIR" not in env and "APPDIR" not in env


def test_server_edition_has_no_desktop_api(monkeypatch):
    from fastapi.testclient import TestClient
    from app.core.app_factory import create_app

    monkeypatch.delenv("SFS_DESKTOP", raising=False)
    client = TestClient(create_app())
    assert client.get("/api/desktop/").status_code in (404, 405)
