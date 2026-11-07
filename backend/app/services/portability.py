"""Moving work between SFS instances.

Flows and projects export as one `<name>.sfs.json` file: the flows themselves,
plus a manifest of what they need (connection names, custom processors, plugin
engines). Importing regenerates every id, imports schedules inactive, and maps
missing connections instead of failing on them.

Connections export as metadata only: no password, no secret option. Imported
connections wait in a "needs credentials" state until someone enters them.

Never exported: frame cache, persisted data, credentials, run history.
"""
from __future__ import annotations

import copy
import re
import uuid

FLOW_FORMAT = "sfs-export"
CONN_FORMAT = "sfs-connections"
FORMAT_VERSION = 1

# format_version n → n+1, applied in order to older files.
_MIGRATIONS: dict[int, callable] = {}


class FormatError(ValueError):
    """The file isn't an SFS export this version can read."""


def _check(bundle: dict, fmt: str, what: str) -> dict:
    if not isinstance(bundle, dict) or bundle.get("format") != fmt:
        raise FormatError(f"This isn't an SFS {what} file.")
    v = bundle.get("format_version")
    if not isinstance(v, int) or v < 1:
        raise FormatError(f"This {what} file has an unknown format_version ({v!r}).")
    if v > FORMAT_VERSION:
        raise FormatError(
            f"This {what} file was written by a newer SFS ({bundle.get('sfs_version') or 'unknown'}, "
            f"format {v}); this one reads up to format {FORMAT_VERSION}. Update SFS to import it.")
    b = copy.deepcopy(bundle)
    while b["format_version"] < FORMAT_VERSION:
        b = _MIGRATIONS[b["format_version"]](b)
        b["format_version"] += 1
    return b


# ── Connection references inside a flow ──────────────────────────────────────
# Node fields that name a connection, and helper calls in code that take one
# as a string literal.

_CODE_REFS = (
    re.compile(r"""\b(?:get_engine|get_storage|fast_read)\(\s*(['"])(?P<name>[^'"\n]+)\1"""),
    re.compile(r"""\bfast_write\(\s*[^,()]+,\s*(['"])(?P<name>[^'"\n]+)\1"""),
    re.compile(r"""\b(?:storage_conn|connection_name)\s*=\s*(['"])(?P<name>[^'"\n]+)\1"""),
)


def _connection_params() -> set[str]:
    """Node-data keys that engines declare as connections (e.g. Database read)."""
    from app.engine.registry import ENGINES, load_plugins
    load_plugins()
    return {p["key"] for spec in ENGINES.values() for p in spec.params
            if p.get("type") == "connection" and p.get("key")}


def _field_refs(node: dict, keys: set[str]):
    """(container, key) pairs holding a connection name in a node's data."""
    d = node.get("data") or {}
    for k in keys | {"target_connection"}:
        if isinstance(d.get(k), str) and d[k]:
            yield d, k
    p = d.get("persist")
    if isinstance(p, dict) and isinstance(p.get("target"), str) and p["target"] not in ("", "local"):
        yield p, "target"


def _codes(flow: dict):
    """(container, key) pairs holding code: node code and shared functions."""
    if isinstance(flow.get("shared_functions"), str):
        yield flow, "shared_functions"
    for n in flow.get("nodes") or []:
        d = n.get("data") or {}
        if isinstance(d.get("code"), str):
            yield d, "code"


def connection_refs(flows: list[dict]) -> list[str]:
    keys = _connection_params()
    names = set()
    for f in flows:
        for n in f.get("nodes") or []:
            names.update(c[k] for c, k in _field_refs(n, keys))
        for c, k in _codes(f):
            for rx in _CODE_REFS:
                names.update(m.group("name") for m in rx.finditer(c[k]))
    return sorted(names)


def rename_connections(flows: list[dict], mapping: dict[str, str]) -> None:
    """Point references at local connections, in place. Unmapped names stay."""
    mapping = {a: b for a, b in mapping.items() if b and a != b}
    if not mapping:
        return
    keys = _connection_params()

    def sub(m: re.Match) -> str:
        new = mapping.get(m.group("name"))
        if not new:
            return m.group(0)
        s, e = m.span("name")
        return m.group(0)[: s - m.start()] + new + m.group(0)[e - m.start():]

    for f in flows:
        for n in f.get("nodes") or []:
            for c, k in _field_refs(n, keys):
                c[k] = mapping.get(c[k], c[k])
        for c, k in _codes(f):
            for rx in _CODE_REFS:
                c[k] = rx.sub(sub, c[k])


# ── Flow and project export ──────────────────────────────────────────────────

def _engines_used(flows: list[dict]) -> list[dict]:
    """Plugin engines the flows use, with the plugin and version providing them here."""
    from app.engine.registry import ENGINES, load_plugins, loaded_plugins
    load_plugins()
    versions = {f"plugin:{p['name']}": p.get("version") for p in loaded_plugins()}
    out = {}
    for f in flows:
        for n in f.get("nodes") or []:
            e = (n.get("data") or {}).get("engine")
            if not e:
                continue
            spec = ENGINES.get(e)
            if spec is not None and spec.source == "builtin":
                continue
            out[e] = {"name": e, "plugin": spec.source.removeprefix("plugin:") if spec else None,
                      "version": versions.get(spec.source) if spec else None}
    return [out[k] for k in sorted(out)]


def _custom_used(flows: list[dict]) -> list[dict]:
    """Custom processors whose code a node holds (they are copied into nodes, not linked)."""
    from app.services import custom_processor_service as cps
    codes = {(n.get("data") or {}).get("code", "").strip()
             for f in flows for n in f.get("nodes") or [] if n.get("type") == "processor"}
    return [{k: c.get(k, "") for k in ("name", "description", "engine", "code")}
            for c in cps.list_custom() if c.get("code", "").strip() and c["code"].strip() in codes]


def export_flows(project: dict, flow_id: str | None = None, *, include_custom: bool = True,
                 sfs_version: str = "") -> dict:
    """One project (or one flow of it) as a portable bundle. Deterministic: the
    same flows export to the same bytes."""
    from app.services.project_service import canonical_flow
    flows = [canonical_flow(f) for f in project.get("flows") or [] if flow_id in (None, f.get("id"))]
    if flow_id and not flows:
        raise KeyError(flow_id)
    custom = _custom_used(flows)
    bundle = {
        "format": FLOW_FORMAT, "format_version": FORMAT_VERSION, "sfs_version": sfs_version,
        "kind": "flow" if flow_id else "project",
        "name": flows[0]["name"] if flow_id else project.get("name", ""),
        "description": "" if flow_id else project.get("description", ""),
        "flows": flows,
        "manifest": {
            "connections": connection_refs(flows),
            "custom_processors": [c["name"] for c in custom],
            "engines": _engines_used(flows),
        },
    }
    if include_custom and custom:
        bundle["custom_processors"] = custom
    return bundle


# ── Flow and project import ──────────────────────────────────────────────────

def read_flows(bundle: dict) -> dict:
    b = _check(bundle, FLOW_FORMAT, "flow export")
    if not isinstance(b.get("flows"), list) or not b["flows"]:
        raise FormatError("This export holds no flows.")
    return b


def preview_flows(bundle: dict, local_connections: list[str]) -> dict:
    """What importing would need: connections to map, engines not installed here."""
    from app.engine.registry import ENGINES, load_plugins
    from app.services import custom_processor_service as cps
    load_plugins()
    b = read_flows(bundle)
    needed = connection_refs(b["flows"])
    have_cp = {c.get("name") for c in cps.list_custom()}
    return {
        "kind": b.get("kind", "project"), "name": b.get("name", ""),
        "sfs_version": b.get("sfs_version"),
        "flows": [f.get("name", "") for f in b["flows"]],
        "connections": needed,
        "missing_connections": [c for c in needed if c not in local_connections],
        "local_connections": sorted(local_connections),
        "missing_engines": [e for e in (b.get("manifest") or {}).get("engines", []) if e["name"] not in ENGINES],
        "custom_processors": [{"name": c.get("name"), "here": c.get("name") in have_cp}
                              for c in b.get("custom_processors") or []],
    }


def _fresh_ids(flow: dict) -> dict:
    """The flow with new flow, node and edge ids; node data that names a node
    by id follows it."""
    f = copy.deepcopy(flow)
    ids = {n["id"]: str(uuid.uuid4()) for n in f.get("nodes") or [] if n.get("id")}

    def remap(v):
        if isinstance(v, str):
            return ids.get(v, v)
        if isinstance(v, list):
            return [remap(x) for x in v]
        if isinstance(v, dict):
            return {k: remap(x) for k, x in v.items()}
        return v

    f["id"] = str(uuid.uuid4())
    for n in f.get("nodes") or []:
        n["id"] = ids.get(n.get("id"), str(uuid.uuid4()))
        if isinstance(n.get("parentNode"), str):
            n["parentNode"] = ids.get(n["parentNode"], n["parentNode"])
        n["data"] = remap(n.get("data") or {})
    for e in f.get("edges") or []:
        e["id"] = str(uuid.uuid4())
        e["source"] = ids.get(e.get("source"), e.get("source"))
        e["target"] = ids.get(e.get("target"), e.get("target"))
    f["active"] = False                      # schedules import inactive, like a trash restore
    return f


def import_flows(bundle: dict, connection_map: dict[str, str | None] | None = None,
                 project_id: str | None = None) -> dict:
    """Import as a new project, or append the flows to `project_id`. Returns the project."""
    from app.services import custom_processor_service as cps
    from app.services import project_service as ps
    b = read_flows(bundle)
    flows = [_fresh_ids(f) for f in b["flows"]]
    rename_connections(flows, connection_map or {})

    have_cp = {c.get("name") for c in cps.list_custom()}
    for c in b.get("custom_processors") or []:
        if c.get("name") and c["name"] not in have_cp and isinstance(c.get("code"), str):
            cps.create_custom({k: c.get(k) for k in ("name", "description", "engine", "code") if c.get(k) is not None})
            have_cp.add(c["name"])

    if project_id is None:
        p = ps.create_project(b.get("name") or "Imported project", b.get("description") or "")
        return ps.save_project(p["id"], {"flows": flows})
    p = ps.get_project(project_id)
    if p is None:
        raise KeyError(project_id)
    taken = {f.get("name") for f in p["flows"]}
    for f in flows:
        base, n = f.get("name") or "Flow", 2
        while f.get("name") in taken:
            f["name"] = f"{base} ({n})"
            n += 1
        taken.add(f["name"])
    return ps.save_project(project_id, {"flows": p["flows"] + flows})


# ── Connections and variables ────────────────────────────────────────────────

async def export_connections(db, variables: bool = True) -> dict:
    from sqlalchemy import select
    from app.models.variable import Variable
    from app.services import connection_service as cs
    out = []
    for c in await cs.list_connections(db):
        pub = cs.public(c)
        n = cs.needs(c)
        out.append({
            **{k: pub[k] for k in ("name", "dialect", "host", "port", "database", "username")},
            "extra_params": pub["extra_params"] or {},
            "browser": pub["browser"],
            "requires": {"password": bool(c.password_enc) or n["password"],
                         "extras": sorted(set(pub["secret_extras"]) | set(n["extras"]))},
        })
    bundle = {"format": CONN_FORMAT, "format_version": FORMAT_VERSION,
              "connections": sorted(out, key=lambda c: c["name"])}
    if variables:
        r = await db.execute(select(Variable).order_by(Variable.scope, Variable.key))
        bundle["variables"] = [{"scope": v.scope, "key": v.key, "value": v.value, "description": v.description}
                               for v in r.scalars().all()]
        bundle["variables_note"] = "Variables are plain text. They must not hold secrets."
    return bundle


async def preview_connections(db, bundle: dict) -> dict:
    from sqlalchemy import select
    from app.models.variable import Variable
    from app.services import connection_service as cs
    b = _check(bundle, CONN_FORMAT, "connections export")
    here = {c.name for c in await cs.list_connections(db)}
    r = await db.execute(select(Variable))
    have_vars = {(v.scope, v.key) for v in r.scalars().all()}
    vs = b.get("variables") or []
    return {
        "connections": [{"name": c["name"], "dialect": c.get("dialect"), "exists": c["name"] in here,
                         "requires": c.get("requires") or {}} for c in b.get("connections") or []],
        "variables": len(vs),
        "variables_existing": sorted(v["key"] for v in vs if (v.get("scope", "global"), v["key"]) in have_vars),
    }


async def import_connections(db, bundle: dict, on_conflict: dict[str, str], variables: bool = True) -> dict:
    from sqlalchemy import select
    from app.models.variable import Variable
    from app.services import connection_service as cs
    from app.services.secret_keys import is_secret
    b = _check(bundle, CONN_FORMAT, "connections export")
    existing = {c.name: c for c in await cs.list_connections(db)}
    report = {"created": [], "overwritten": [], "renamed": {}, "skipped": [],
              "variables_added": 0, "variables_skipped": 0}

    for c in b.get("connections") or []:
        name = c.get("name")
        if not name or not c.get("dialect"):
            continue
        req = c.get("requires") or {}
        fields = {k: c.get(k) for k in ("dialect", "host", "port", "database", "username")}
        plain = {k: v for k, v in (c.get("extra_params") or {}).items()
                 if not is_secret(k) and not cs._secret_key(k, c.get("dialect"))}
        action = on_conflict.get(name, "skip") if name in existing else "create"
        if action.startswith("rename:"):
            new = action.split(":", 1)[1].strip()
            if not new or new in existing:
                report["skipped"].append(name)
                continue
            report["renamed"][name] = new
            name, action = new, "create"
        if action == "create":
            conn = await cs.create_connection(db, {
                "name": name, **fields, "extra_params": plain, "browser": c.get("browser"),
                "needs": {"password": bool(req.get("password")), "extras": list(req.get("extras") or [])}})
            existing[name] = conn
            if name not in report["renamed"].values():
                report["created"].append(name)
        elif action == "overwrite":
            conn = existing[name]
            for k, v in fields.items():
                setattr(conn, k, v)
            conn.extra_params = cs.merge_extras(conn, plain, [])       # keeps stored secrets
            have = set(cs._raw_extras(conn))
            missing = {"password": bool(req.get("password")) and not conn.password_enc,
                       "extras": [k for k in req.get("extras") or [] if k not in have]}
            cs._set_options(conn, browser=c.get("browser") or None,
                            needs=missing if (missing["password"] or missing["extras"]) else None)
            await db.commit()
            report["overwritten"].append(name)
        else:
            report["skipped"].append(name)

    if variables:
        r = await db.execute(select(Variable))
        have = {(v.scope, v.key) for v in r.scalars().all()}
        for v in b.get("variables") or []:
            key, scope = v.get("key"), v.get("scope") or "global"
            if not key or (scope, key) in have:
                report["variables_skipped"] += 1
                continue
            db.add(Variable(id=str(uuid.uuid4()), scope=scope, key=key,
                            value=v.get("value"), description=v.get("description")))
            have.add((scope, key))
            report["variables_added"] += 1
        await db.commit()
    return report
