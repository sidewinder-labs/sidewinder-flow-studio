"""Every API route is guarded and names a permission from the catalogue, so a
new route can't skip sign-in or roles by accident."""
from fastapi.routing import APIRoute, APIWebSocketRoute

from app.auth.guard import GUARD_ATTR
from app.auth.permissions import CATALOGUE, permission_for

PUBLIC = {("GET", "/api/health"), ("GET", "/api/auth/status"), ("POST", "/api/auth/login"),
          ("POST", "/api/auth/setup"), ("POST", "/api/auth/logout")}


def _routes(routes, prefix="", deps=()):
    """(route, full path, router-level dependencies), through included routers."""
    for r in routes:
        if hasattr(r, "include_context"):                    # FastAPI keeps included routers nested
            ctx = r.include_context
            yield from _routes(r.original_router.routes, prefix + (ctx.prefix or ""),
                               (*deps, *ctx.dependencies))
        elif isinstance(r, (APIRoute, APIWebSocketRoute)):
            yield r, prefix + r.path, deps


def _app(monkeypatch):
    monkeypatch.setenv("SFS_DESKTOP", "1")                   # include the desktop routes too
    from app.core.app_factory import create_app
    return create_app()


def test_every_api_route_is_guarded_with_a_known_permission(monkeypatch):
    seen, unguarded, unknown = 0, [], []
    for route, path, deps in _routes(_app(monkeypatch).routes):
        if isinstance(route, APIWebSocketRoute) or not path.startswith("/api/"):
            continue
        guards = [getattr(d.dependency, GUARD_ATTR) for d in (*deps, *route.dependencies)
                  if hasattr(d.dependency, GUARD_ATTR)]
        for method in route.methods - {"HEAD", "OPTIONS"}:
            seen += 1
            if (method, path) in PUBLIC:
                continue
            if not guards:
                unguarded.append(f"{method} {path}")
                continue
            perm = permission_for(guards[0], method, route.endpoint)
            if perm is None:
                unguarded.append(f"{method} {path} (marked public)")
            elif perm not in CATALOGUE:
                unknown.append(f"{method} {path}: {perm}")
    assert seen > 80
    assert not unguarded, f"routes without a permission: {unguarded}"
    assert not unknown, f"permissions not in the catalogue: {unknown}"


def test_the_websocket_checks_sign_in():
    import inspect
    from app.api import ws
    assert "authorize_ws" in inspect.getsource(ws.flow_ws)


def test_reads_and_changes_need_different_permissions(monkeypatch):
    """Spot checks of the mapping (the defaults and the @needs overrides)."""
    by_route = {}
    for route, path, deps in _routes(_app(monkeypatch).routes):
        g = [getattr(d.dependency, GUARD_ATTR) for d in deps if hasattr(d.dependency, GUARD_ATTR)]
        if g and isinstance(route, APIRoute):
            for m in route.methods:
                by_route[(m, path)] = permission_for(g[0], m, route.endpoint)
    assert by_route[("GET", "/api/projects/{pid}")] == "projects.view"
    assert by_route[("PUT", "/api/projects/{pid}")] == "projects.edit"
    assert by_route[("POST", "/api/execute/run")] == "flows.run"
    assert by_route[("POST", "/api/connections/{cid}/test")] == "connections.use"
    assert by_route[("POST", "/api/explore/query")] == "data.query"
    assert by_route[("POST", "/api/explore/node")] == "projects.edit"
    assert by_route[("POST", "/api/agent/providers")] == "settings.admin"
    assert by_route[("PUT", "/api/plugins/licence")] == "plugins.admin"
    assert by_route[("PUT", "/api/settings/")] == "settings.admin"
    assert by_route[("POST", "/api/auth/tokens")] == "auth.manage"
