"""What a request may do: the permission catalogue, and which permission each
route needs.

Every API router is mounted with `guard(area)` (app_factory.py). A route needs
its area's view permission for GET/HEAD and its edit permission otherwise;
`@needs("…")` under the route decorator overrides that for one route.
tests/test_auth_routes.py fails if a route has no guard or names a permission
that isn't in CATALOGUE.

The community password provider grants every permission. Providers with roles
(sfs-enterprise) map roles onto these names, so a role is a set of entries
from this catalogue and nothing else.

Node code runs on the server with the server's rights: anyone holding
flows.run on a flow they can also edit (projects.edit) can do whatever the
server can. Roles must be described that way, never as a sandbox.
"""
from __future__ import annotations

from typing import Callable

CATALOGUE: dict[str, str] = {
    "app.view":         "Use the app: capabilities, engine list, code helpers",
    "projects.view":    "Open projects and flows",
    "projects.edit":    "Create, change and delete projects and flows, node code included",
    "flows.run":        "Run flows, clear their cache",
    "data.view":        "See node outputs, run events and Explore sources",
    "data.query":       "Run Explore and drill queries on frames and connections",
    "runs.view":        "See the run history",
    "connections.view": "See connections (never their secrets)",
    "connections.use":  "Test a connection",
    "connections.edit": "Create, change, import and delete connections",
    "variables.view":   "See variables",
    "variables.edit":   "Create and delete variables",
    "library.view":     "See the function library and custom processors",
    "library.edit":     "Change the function library and custom processors",
    "ai.view":          "See the AI providers",
    "ai.use":           "Generate code and flows with the AI agent",
    "plugins.view":     "See installed plugins and the catalog",
    "plugins.admin":    "Install, update and remove plugins; the licence key",
    "settings.view":    "See settings",
    "settings.admin":   "Change settings, AI providers; restart SFS",
    "admin.view":       "Diagnostics",
    "auth.manage":      "Change the password and manage API tokens",
}

ALL = frozenset(CATALOGUE)

# area → (permission to read, permission to change)
AREAS: dict[str, tuple[str, str]] = {
    "projects":          ("projects.view", "projects.edit"),
    "connections":       ("connections.view", "connections.edit"),
    "execute":           ("data.view", "flows.run"),
    "metadata":          ("variables.view", "variables.edit"),
    "agent":             ("ai.view", "ai.use"),
    "runs":              ("runs.view", "runs.view"),
    "custom_processors": ("library.view", "library.edit"),
    "function_library":  ("library.view", "library.edit"),
    "capabilities":      ("app.view", "app.view"),
    "diagnostics":       ("admin.view", "admin.view"),
    "plugins":           ("plugins.view", "plugins.admin"),
    "settings":          ("settings.view", "settings.admin"),
    "explore":           ("data.view", "data.query"),
    "desktop":           ("settings.view", "settings.admin"),
    "auth":              ("auth.manage", "auth.manage"),
}

WS_PERMISSION = "data.view"           # the run-event WebSocket

_ATTR = "__sfs_permission__"


def needs(permission: str) -> Callable:
    """Mark one route as needing `permission` instead of its area's default.
    Put it directly above the function, below the @router decorator."""
    if permission not in CATALOGUE:
        raise ValueError(f"Unknown permission '{permission}'")

    def mark(fn: Callable) -> Callable:
        setattr(fn, _ATTR, permission)
        return fn
    return mark


def public(fn: Callable) -> Callable:
    """Mark a route that needs no sign-in (signing in itself, the status)."""
    setattr(fn, _ATTR, None)
    return fn


def permission_for(area: str, method: str, endpoint: Callable | None) -> str | None:
    """The permission a request needs; None for a public route."""
    if endpoint is not None and hasattr(endpoint, _ATTR):
        return getattr(endpoint, _ATTR)
    view, edit = AREAS[area]
    return view if method in ("GET", "HEAD", "OPTIONS") else edit
