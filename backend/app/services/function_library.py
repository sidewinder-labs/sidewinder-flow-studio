"""Function library: reusable functions copied into flows, never linked.

Inserting a library function copies its code into the flow's Functions (the
flow's `shared_functions`) and adds the imports it needs to the flow's Imports.
The copy belongs to the flow: library edits can't break a flow, and a project
file runs anywhere without the library. Each copy records where it came from
(`flow["function_provenance"][name] = {library_id, version, hash, inserted_at}`),
so a library fix can be pulled in deliberately, never automatically.

Entries are stored as one JSON file next to custom processors (the data
directory on desktop, the data volume in Docker).
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings


class LibraryError(ValueError):
    pass


# ── Reading code ─────────────────────────────────────────────────────────────

def _parse(code: str, what: str = "code") -> ast.Module:
    try:
        return ast.parse(code or "")
    except SyntaxError as exc:
        raise LibraryError(f"The {what} doesn't parse: line {exc.lineno}: {exc.msg}") from None


def top_level(code: str) -> list[dict]:
    """Top-level def/class blocks with their exact source (decorators,
    docstrings and inner comments included): [{name, kind, source, start, end}]."""
    tree = _parse(code)
    lines = (code or "").splitlines(keepends=True)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            source = "".join(lines[start - 1:node.end_lineno]).rstrip() + "\n"
            out.append({"name": node.name, "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                        "source": source, "start": start, "end": node.end_lineno})
    return out


def content_hash(code: str) -> str:
    """Hash that ignores trailing whitespace and blank lines at either end."""
    norm = "\n".join(l.rstrip() for l in (code or "").strip("\n").splitlines())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def _used_names(code: str) -> set[str]:
    names = set()
    for node in ast.walk(_parse(code)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                names.add(root.id)
    return names


def _import_statements(code: str) -> list[ast.stmt]:
    return [n for n in _parse(code, "Imports tab").body if isinstance(n, (ast.Import, ast.ImportFrom))]


def _binds(stmt: ast.stmt) -> list[tuple[str, ast.alias]]:
    """(bound name, alias) for each name an import statement binds."""
    out = []
    for a in stmt.names:
        if isinstance(stmt, ast.Import):
            out.append((a.asname or a.name.split(".")[0], a))
        elif a.name != "*":
            out.append((a.asname or a.name, a))
    return out


def propose_imports(function_code: str, flow_imports: str) -> str:
    """The import statements from the flow's Imports tab that this code uses,
    trimmed to the names it uses (one statement per line)."""
    used = _used_names(function_code)
    keep = []
    for stmt in _import_statements(flow_imports):
        aliases = [a for name, a in _binds(stmt) if name in used]
        if aliases:
            node = (ast.Import(names=aliases) if isinstance(stmt, ast.Import)
                    else ast.ImportFrom(module=stmt.module, names=aliases, level=stmt.level))
            keep.append(ast.unparse(node))
    return "\n".join(dict.fromkeys(keep)) + ("\n" if keep else "")


def missing_imports(needed: str, flow_imports: str) -> list[str]:
    """Statements of `needed` whose names the flow's imports don't already bind."""
    bound = {name for stmt in _import_statements(flow_imports) for name, _ in _binds(stmt)}
    existing = {ast.unparse(s) for s in _import_statements(flow_imports)}
    out = []
    for stmt in _import_statements(needed):
        text = ast.unparse(stmt)
        names = [n for n, _ in _binds(stmt)]
        if text not in existing and not (names and all(n in bound for n in names)):
            out.append(text)
    return out


# ── Storage ──────────────────────────────────────────────────────────────────

def _store_path() -> Path:
    d = Path(settings.PROJECTS_DIR).parent
    d.mkdir(parents=True, exist_ok=True)
    return d / "function_library.json"


def _load() -> list[dict]:
    p = _store_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    _store_path().write_text(json.dumps(items, indent=2, sort_keys=True))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def get(entry_id: str) -> dict | None:
    return next((e for e in _load() if e["id"] == entry_id), None)


def find_by_name(name: str) -> dict | None:
    return next((e for e in _load() if e["name"] == name), None)


def _validate(name: str, code: str, imports: str) -> None:
    if not name.isidentifier():
        raise LibraryError(f"'{name}' isn't a valid Python name")
    names = [b["name"] for b in top_level(code)]
    if name not in names:
        raise LibraryError(f"The code must define a top-level function or class called '{name}'"
                           + (f" (it defines {', '.join(names)})" if names else ""))
    non_defs = [n for n in _parse(code).body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))]
    if non_defs:
        raise LibraryError("Library code may hold only def and class blocks; put imports in the imports field")
    _import_statements(imports)


def create(*, name: str, code: str, description: str = "", imports: str = "", tags=()) -> dict:
    """A new entry. A name already in the library is refused: save a new
    version of that entry instead, or pick another name."""
    _validate(name, code, imports)
    items = _load()
    if any(e["name"] == name for e in items):
        raise LibraryError(f"The library already has '{name}'")
    entry = {"id": str(uuid.uuid4()), "name": name, "description": description, "code": code,
             "imports": imports, "tags": sorted({t.strip() for t in tags if t.strip()}), "version": 1,
             "content_hash": content_hash(code), "deprecated": False, "deprecation_note": "",
             "created_at": _now(), "updated_at": _now()}
    items.append(entry)
    _save(items)
    return entry


def update(entry_id: str, **fields) -> dict:
    """Edit an entry: every edit is a new version. Flows holding copies then
    show 'update available' and update only when their owner chooses."""
    items = _load()
    entry = next((e for e in items if e["id"] == entry_id), None)
    if entry is None:
        raise KeyError(entry_id)
    new = {**entry, **{k: v for k, v in fields.items() if v is not None and k in
                       ("name", "description", "code", "imports", "tags")}}
    if "tags" in fields and fields["tags"] is not None:
        new["tags"] = sorted({t.strip() for t in fields["tags"] if t.strip()})
    _validate(new["name"], new["code"], new["imports"])
    if new["name"] != entry["name"] and any(e["name"] == new["name"] for e in items):
        raise LibraryError(f"The library already has '{new['name']}'")
    new.update(version=entry["version"] + 1, content_hash=content_hash(new["code"]), updated_at=_now())
    items[items.index(entry)] = new
    _save(items)
    return new


def deprecate(entry_id: str, note: str = "", deprecated: bool = True) -> dict:
    items = _load()
    entry = next((e for e in items if e["id"] == entry_id), None)
    if entry is None:
        raise KeyError(entry_id)
    entry.update(deprecated=deprecated, deprecation_note=note if deprecated else "", updated_at=_now())
    _save(items)
    return entry


def delete(entry_id: str) -> bool:
    """Remove an entry. Flows keep their copies, which keep working."""
    items = _load()
    remaining = [e for e in items if e["id"] != entry_id]
    if len(remaining) == len(items):
        return False
    _save(remaining)
    return True


# ── Copies in flows ──────────────────────────────────────────────────────────

def _rename(code: str, old: str, new: str) -> str:
    """Rename the top-level def/class `old` in a library entry's code."""
    pattern = re.compile(rf"^((?:async\s+)?def|class)(\s+){re.escape(old)}\b", re.M)
    out, n = pattern.subn(rf"\g<1>\g<2>{new}", code, count=1)
    if not n:
        raise LibraryError(f"Couldn't find '{old}' to rename")
    return out


def _append(functions: str, code: str) -> str:
    functions = (functions or "").rstrip()
    return f"{functions}\n\n\n{code.strip()}\n" if functions else code.strip() + "\n"


def _replace_block(functions: str, name: str, code: str) -> str:
    block = next((b for b in top_level(functions) if b["name"] == name), None)
    if block is None:
        return _append(functions, code)
    lines = functions.splitlines(keepends=True)
    return "".join(lines[:block["start"] - 1]) + code.rstrip() + "\n" + "".join(lines[block["end"]:])


def insert(*, functions: str, imports: str, provenance: dict, entry_id: str,
           on_conflict: str = "ask", new_name: str | None = None) -> dict:
    """Copy a library entry into a flow. on_conflict, when the flow already has
    a function of that name: 'ask' (refuse, so the user chooses), 'replace',
    or 'rename' (insert under new_name). Returns the flow's new functions,
    imports and provenance, plus the imports added."""
    entry = get(entry_id)
    if entry is None:
        raise KeyError(entry_id)
    name, code = entry["name"], entry["code"]
    existing = {b["name"] for b in top_level(functions)}
    renamed = False
    if name in existing:
        if on_conflict == "ask":
            raise LibraryError(f"conflict:{name}")
        if on_conflict == "rename":
            if not new_name or not new_name.isidentifier() or new_name in existing:
                raise LibraryError("Pick a new name that is a valid Python name not used in this flow")
            code, name, renamed = _rename(code, name, new_name), new_name, True
    new_functions = _replace_block(functions, name, code) if name in existing else _append(functions, code)
    added = missing_imports(entry["imports"], imports)
    new_imports = (imports.rstrip() + "\n" + "\n".join(added) + "\n") if added else imports
    prov = dict(provenance or {})
    # A copy inserted under another name is the flow's own, by the same rule as a rename.
    if renamed:
        prov.pop(name, None)
    else:
        prov[name] = {"library_id": entry["id"], "version": entry["version"],
                      "hash": content_hash(code), "inserted_at": _now()}
    return {"functions": new_functions, "imports": new_imports, "provenance": dict(sorted(prov.items())),
            "added_imports": added, "name": name}


def prune(functions: str, provenance: dict | None) -> dict:
    """Provenance for functions still in the flow under their library name.
    Renaming or removing a function drops its provenance: it is the flow's own."""
    try:
        names = {b["name"] for b in top_level(functions)}
    except LibraryError:
        return dict(provenance or {})                      # unparseable mid-edit: keep as is
    return dict(sorted((k, v) for k, v in (provenance or {}).items() if k in names))


def copy_states(functions: str, provenance: dict | None) -> dict:
    """Per function from the library: its state and what can be done.
    current | update | modified | deleted, plus deprecated and the note."""
    blocks = {b["name"]: b for b in top_level(functions)}
    library = {e["id"]: e for e in _load()}
    out = {}
    for name, rec in (provenance or {}).items():
        block = blocks.get(name)
        if block is None:
            continue
        entry = library.get(rec.get("library_id"))
        if entry is None:
            out[name] = {"state": "deleted"}
            continue
        if content_hash(block["source"]) != rec.get("hash"):
            state = "modified"
        elif entry["version"] > rec.get("version", 0):
            state = "update"
        else:
            state = "current"
        out[name] = {"state": state, "library_id": entry["id"], "version": rec.get("version"),
                     "latest_version": entry["version"], "deprecated": entry["deprecated"],
                     "deprecation_note": entry["deprecation_note"], "library_code": entry["code"],
                     "library_imports": entry["imports"]}
    return out


def update_copy(*, functions: str, imports: str, provenance: dict, name: str) -> dict:
    """Replace the flow's copy with the library's current version."""
    rec = (provenance or {}).get(name)
    entry = get(rec["library_id"]) if rec else None
    if entry is None:
        raise LibraryError(f"'{name}' has no library entry to update from")
    code = entry["code"] if entry["name"] == name else _rename(entry["code"], entry["name"], name)
    new_functions = _replace_block(functions, name, code)
    added = missing_imports(entry["imports"], imports)
    new_imports = (imports.rstrip() + "\n" + "\n".join(added) + "\n") if added else imports
    prov = dict(provenance)
    prov[name] = {"library_id": entry["id"], "version": entry["version"], "hash": content_hash(code),
                  "inserted_at": _now()}
    return {"functions": new_functions, "imports": new_imports, "provenance": dict(sorted(prov.items())),
            "added_imports": added, "name": name}


# ── Usage across projects ────────────────────────────────────────────────────

def usage() -> dict[str, dict]:
    """For each library id: the flows holding a copy, each with its state, and
    when it was last inserted. Informational: copies are independent."""
    from app.services import project_service
    out: dict[str, dict] = {}
    for p in project_service.list_projects():
        project = project_service.get_project(p["id"]) or {}
        for flow in project.get("flows", []):
            prov = flow.get("function_provenance") or {}
            if not prov:
                continue
            try:
                states = copy_states(flow.get("shared_functions", ""), prov)
            except LibraryError:
                states = {}
            for name, rec in prov.items():
                lid = rec.get("library_id")
                u = out.setdefault(lid, {"count": 0, "copies": [], "last_inserted": None})
                u["count"] += 1
                u["copies"].append({"project_id": project["id"], "project": project.get("name"),
                                    "flow_id": flow["id"], "flow": flow.get("name"), "function": name,
                                    "state": states.get(name, {}).get("state", "unknown")})
                if rec.get("inserted_at") and (u["last_inserted"] or "") < rec["inserted_at"]:
                    u["last_inserted"] = rec["inserted_at"]
    return out


def list_entries(include_deprecated: bool = True) -> list[dict]:
    use = usage()
    out = []
    for e in sorted(_load(), key=lambda e: e["name"].lower()):
        if e["deprecated"] and not include_deprecated:
            continue
        u = use.get(e["id"], {"count": 0, "copies": [], "last_inserted": None})
        out.append({**e, "usage": u})
    return out


# ── Export / import (with custom processors) ─────────────────────────────────

def export_all() -> dict:
    from app.services import custom_processor_service
    return {"format": "sfs-library", "version": 1, "exported_at": _now(),
            "functions": _load(), "custom_processors": custom_processor_service.list_custom()}


def import_all(data: dict) -> dict:
    """Merge an exported library: entries with a new id are added; an id already
    here is kept as it is (yours wins). Returns counts."""
    from app.services import custom_processor_service
    if data.get("format") != "sfs-library":
        raise LibraryError("Not an SFS library file")
    items, have = _load(), {e["id"] for e in _load()}
    names = {e["name"] for e in items}
    added = skipped = 0
    for e in data.get("functions", []):
        if e.get("id") in have or e.get("name") in names:
            skipped += 1
            continue
        _validate(e["name"], e["code"], e.get("imports", ""))
        items.append(e); names.add(e["name"]); added += 1
    _save(items)
    procs = custom_processor_service.import_many(data.get("custom_processors", []))
    return {"functions_added": added, "functions_skipped": skipped, "custom_processors_added": procs}
