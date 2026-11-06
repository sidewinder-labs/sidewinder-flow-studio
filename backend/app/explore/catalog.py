"""Explore's table browser: schemas first, objects per schema on expand, and
search run in the database rather than in the browser.

PostgreSQL lists declarative partitions (and legacy inheritance children) as
ordinary tables, so a partitioned database would flood the list. Here they
are hidden by default and the parent (`relkind = 'p'`) is shown: the table
people query. ClickHouse parts, MySQL partitions and Trino/Hive partitions are
not catalogued as tables, so other dialects go through SQLAlchemy's inspector.

Settings per connection (connection_service.browser_settings):
show_partitions, show_views, show_system, include_schemas, exclude_schemas.
"""
from __future__ import annotations

import logging

log = logging.getLogger("sfs.explore")

SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "system", "sys", "mysql",
                  "performance_schema", "INFORMATION_SCHEMA"}

SEARCH_LIMIT = 300
SCHEMA_LIMIT = 2000


def _system(schema: str) -> bool:
    return schema in SYSTEM_SCHEMAS or schema.startswith("pg_")


def _schema_ok(schema: str, s: dict) -> bool:
    if not s.get("show_system") and _system(schema):
        return False
    inc, exc = s.get("include_schemas") or [], s.get("exclude_schemas") or []
    return (not inc or schema in inc) and schema not in exc


# ── PostgreSQL: the catalog, with partitions and views told apart ───────────

_PG_KIND = {"r": "table", "p": "table", "f": "table", "v": "view", "m": "materialized_view"}

_PG_SQL = """
SELECT n.nspname, c.relname, c.relkind,
       EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid) AS is_child,
       CASE WHEN c.relkind = 'p'
            THEN (SELECT sum(GREATEST(ch.reltuples, 0))::bigint FROM pg_inherits i
                  JOIN pg_class ch ON ch.oid = i.inhrelid WHERE i.inhparent = c.oid)
            ELSE NULLIF(c.reltuples, -1)::bigint END AS est
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ({kinds}) {where}
ORDER BY n.nspname, c.relname
"""


def _pg_where(s: dict, schema: str | None, q: str | None) -> tuple[str, dict]:
    w, p = [], {}
    if not s.get("show_partitions"):
        w.append("NOT c.relispartition AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)")
    if not s.get("show_system"):
        w.append("n.nspname NOT IN ('information_schema') AND n.nspname NOT LIKE 'pg\\_%'")
    if schema is not None:
        w.append("n.nspname = :schema")
        p["schema"] = schema
    if q:
        w.append("(c.relname ILIKE :q OR n.nspname ILIKE :q)")
        p["q"] = f"%{q}%"
    return "".join(f" AND {x}" for x in w), p


def _pg_kinds(s: dict) -> str:
    return "'r','p','f'" + (",'v','m'" if s.get("show_views", True) else "")


def _pg_objects(engine, s: dict, schema: str | None, q: str | None, limit: int) -> dict:
    import sqlalchemy as sa
    where, params = _pg_where(s, schema, q)
    sql = _PG_SQL.format(kinds=_pg_kinds(s), where=where) + f" LIMIT {int(limit) + 1}"
    with engine.connect() as c:
        rows = c.execute(sa.text(sql), params).fetchall()
    out = []
    for sch, name, kind, is_child, est in rows:
        if not _schema_ok(sch, s):
            continue
        out.append({"schema": sch, "name": name,
                    "kind": "partition" if is_child else _PG_KIND.get(kind, "table"),
                    "partitioned": kind == "p", "rows": int(est) if est is not None else None})
    return {"tables": out[:limit], "truncated": len(out) > limit}


def _pg_schemas(engine, s: dict) -> list[dict]:
    import sqlalchemy as sa
    where, params = _pg_where(s, None, None)
    sql = (f"SELECT n.nspname, count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
           f"WHERE c.relkind IN ({_pg_kinds(s)}) {where} GROUP BY n.nspname ORDER BY n.nspname")
    with engine.connect() as c:
        rows = c.execute(sa.text(sql), params).fetchall()
    return [{"schema": sch, "count": int(n)} for sch, n in rows if _schema_ok(sch, s)]


# ── Everything else: SQLAlchemy's inspector ──────────────────────────────────

def _insp_objects(engine, dialect: str, s: dict, schema: str) -> list[dict]:
    import sqlalchemy as sa
    from app.explore.live import _estimates
    insp = sa.inspect(engine)
    est = _estimates(engine, dialect)
    kinds = [("table", insp.get_table_names)]
    if s.get("show_views", True):
        kinds.append(("view", insp.get_view_names))
        kinds.append(("materialized_view", getattr(insp, "get_materialized_view_names", None)))
    out, seen = [], set()
    for kind, fn in kinds:
        if fn is None:
            continue
        try:
            names = fn(schema=schema)
        except (NotImplementedError, AttributeError):
            continue
        for name in sorted(names):
            if (schema, name) in seen:            # some dialects list materialized views as views too
                continue
            seen.add((schema, name))
            out.append({"schema": schema, "name": name, "kind": kind, "partitioned": False,
                        "rows": est.get((schema, name))})
    return out


def _insp_schemas(engine, s: dict) -> list[str]:
    import sqlalchemy as sa
    return [x for x in sa.inspect(engine).get_schema_names() if _schema_ok(x, s)]


# ── Entry points ─────────────────────────────────────────────────────────────

def schemas(engine, dialect: str, s: dict) -> list[dict]:
    """Visible schemas, with object counts where the catalog gives them cheaply."""
    if dialect == "postgres":
        return _pg_schemas(engine, s)
    return [{"schema": x, "count": None} for x in _insp_schemas(engine, s)]


def objects(engine, dialect: str, s: dict, schema: str | None = None, q: str | None = None,
            limit: int | None = None) -> dict:
    """Tables and views: of one schema, matching `q` across schemas, or (neither) all."""
    q = (q or "").strip() or None
    limit = limit or (SEARCH_LIMIT if q else SCHEMA_LIMIT)
    if dialect == "postgres":
        return _pg_objects(engine, s, schema, q, limit)
    out, truncated = [], False
    for sch in ([schema] if schema is not None else _insp_schemas(engine, s)):
        if not _schema_ok(sch, s):
            continue
        for o in _insp_objects(engine, dialect, s, sch):
            if q and q.lower() not in f"{o['schema']}.{o['name']}".lower():
                continue
            if len(out) >= limit:
                truncated = True
                break
            out.append(o)
        if truncated:
            break
    return {"tables": out, "truncated": truncated}
