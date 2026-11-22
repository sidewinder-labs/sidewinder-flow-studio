"""Who is making a request."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.auth.permissions import ALL


@dataclass(frozen=True)
class Principal:
    id: str                                   # stable: sessions and tokens point at it
    name: str                                 # shown in the app and the run history
    kind: str = "user"                        # user | token | service | local
    permissions: frozenset[str] = field(default=ALL)
    via: str = ""                             # session | token | none — how it signed in
    session_hash: str | None = None           # the session, when via == "session"

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "via": self.via}


# No sign-in (desktop, or SFS_AUTH=none): one local user who may do everything.
LOCAL = Principal(id="local", name="Local user", kind="local", via="none")

# The scheduler: runs it starts are recorded under this name.
SCHEDULER = Principal(id="scheduler", name="Scheduler", kind="service", via="none")
