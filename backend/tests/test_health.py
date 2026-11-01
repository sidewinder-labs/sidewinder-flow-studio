"""The container healthcheck endpoint, and that it stays out of the access log."""
import logging

from fastapi.testclient import TestClient

from app.core.app_factory import HEALTH_PATH, create_app


def test_health_ok():
    client = TestClient(create_app())
    r = client.get(HEALTH_PATH)
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_health_requests_are_not_access_logged():
    create_app()
    create_app()                                   # the filter is added once
    access = logging.getLogger("uvicorn.access")
    assert len(access.filters) == 1

    def line(path):
        # uvicorn's access record: client, method, path, http version, status
        return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0,
                                 '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", path, "1.1", 200), None)
    assert not access.filters[0].filter(line(HEALTH_PATH))
    assert access.filters[0].filter(line("/api/projects/"))
