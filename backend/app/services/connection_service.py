import json, uuid, base64
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.connection import Connection


def _fernet() -> Fernet:
    key = settings.SECRET_KEY.encode()[:32].ljust(32, b"0")
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(s: str) -> str:
    return _fernet().encrypt(s.encode()).decode()


BROKEN_URL_PREFIX = "sfs-broken://"


class CredentialError(ValueError):
    """A stored credential exists but cannot be used (wrong SECRET_KEY etc)."""


def decrypt(s: str) -> str:
    try:
        return _fernet().decrypt(s.encode()).decode()
    except InvalidToken:
        raise CredentialError(
            "its stored password can't be decrypted — SECRET_KEY has changed since "
            "it was saved. Edit the connection and re-enter the password, or restore "
            "the original SECRET_KEY in .env."
        ) from None


# ── Options (extra_params) with secret values encrypted at rest ──────────────
# Stored JSON keeps non-secret values as they are and wraps each secret value
# as {"$enc": "<fernet token>"}, with the same key as passwords.

_ENC = "$enc"


def _raw_extras(conn) -> dict:
    raw = getattr(conn, "extra_params", None)
    return json.loads(raw) if raw else {}


def read_extras(conn) -> dict:
    """The connection's options with secrets decrypted, for building engines.
    Raises CredentialError when a secret can't be decrypted."""
    out = {}
    for k, v in _raw_extras(conn).items():
        if isinstance(v, dict) and _ENC in v:
            try:
                v = _fernet().decrypt(v[_ENC].encode()).decode()
            except InvalidToken:
                raise CredentialError(
                    f"its stored option '{k}' can't be decrypted — SECRET_KEY has changed since "
                    "it was saved. Edit the connection and re-enter it, or restore the original "
                    "SECRET_KEY in .env.") from None
        out[k] = v
    return out


def _seal(extras: dict) -> str | None:
    """Options dict → stored JSON, encrypting secret values not yet encrypted."""
    from app.services.secret_keys import is_secret
    out = {}
    for k, v in extras.items():
        if v in (None, ""):
            continue
        if is_secret(k) and not (isinstance(v, dict) and _ENC in v):
            v = {_ENC: encrypt(str(v))}
        out[k] = v
    return json.dumps(out, sort_keys=True) if out else None


def options(conn) -> dict:
    raw = getattr(conn, "options", None)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def needs(conn) -> dict:
    """Credentials an imported connection still lacks: {"password": bool, "extras": [names]}."""
    n = options(conn).get("needs") or {}
    return {"password": bool(n.get("password")), "extras": list(n.get("extras") or [])}


def needs_credentials(conn) -> bool:
    n = needs(conn)
    return n["password"] or bool(n["extras"])


def public(conn) -> dict:
    """What the API returns: never a password or a secret option's value —
    only the names of the secret options that are set."""
    from app.services.secret_keys import is_secret
    raw = _raw_extras(conn)
    return {
        "id": conn.id, "name": conn.name, "dialect": conn.dialect,
        "host": conn.host, "port": conn.port, "database": conn.database, "username": conn.username,
        "extra_params": {k: v for k, v in raw.items() if not is_secret(k)} or None,
        "secret_extras": sorted(k for k in raw if is_secret(k)),
        "has_password": bool(conn.password_enc),
        "browser": browser_settings(conn),
        "needs": needs(conn),
        "needs_credentials": needs_credentials(conn),
    }


BROWSER_DEFAULTS = {"show_partitions": False, "show_views": True, "show_system": False,
                    "include_schemas": [], "exclude_schemas": []}


def browser_settings(conn) -> dict:
    """Explore's table browser settings for this connection over the defaults."""
    b = options(conn).get("browser") or {}
    return {**BROWSER_DEFAULTS, **{k: v for k, v in b.items() if k in BROWSER_DEFAULTS}}


def _set_options(conn, **parts) -> None:
    o = options(conn)
    for k, v in parts.items():
        if v is None:
            o.pop(k, None)
        else:
            o[k] = v
    conn.options = json.dumps(o, sort_keys=True) if o else None


def encrypt_plaintext_extras(sync_conn) -> int:
    """One-time migration (run at startup, idempotent): encrypt secret option
    values stored before options were encrypted. Returns how many rows changed."""
    import sqlalchemy as sa
    from app.services.secret_keys import is_secret
    rows = sync_conn.execute(sa.text("SELECT id, extra_params FROM connections "
                                     "WHERE extra_params IS NOT NULL")).fetchall()
    changed = 0
    for cid, raw in rows:
        try:
            extras = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(extras, dict):
            continue
        if any(is_secret(k) and not (isinstance(v, dict) and _ENC in v) and v not in (None, "")
               for k, v in extras.items()):
            sync_conn.execute(sa.text("UPDATE connections SET extra_params = :e WHERE id = :i"),
                              {"e": _seal(extras), "i": cid})
            changed += 1
    return changed


def build_url(conn: Connection) -> str:
    """
    Build a SQLAlchemy URL per dialect. Field conventions:
      snowflake : host=account identifier, database="DB/SCHEMA",
                  extra_params: warehouse, role
      bigquery  : host=GCP project, database=dataset,
                  extra_params: credentials_path (service-account JSON)
      clickhouse: host, port (8123 http / 8443 https), database,
                  extra_params: protocol=https for secure endpoints
      redshift  : host=cluster endpoint, port=5439, database
      mssql     : extra_params: driver (default "ODBC Driver 18 for SQL Server")
      oracle    : database field = service name (thin oracledb, no client install)
      starrocks : MySQL protocol — host=FE host, port=9030, database=db
      doris     : MySQL protocol — host=FE host, port=9030, database=db
      trino     : host=coordinator, database="catalog/schema", extras: http_scheme=https
      databricks: host=workspace host, password=access token,
                  extras: http_path (required), catalog, schema
      adls      : NOT a SQL engine — use get_storage(name) in processors instead
      delta/iceberg : table FORMATS, not DBs — read via a query engine (Trino/
                  StarRocks/Databricks/Snowflake) OR directly with
                  read_delta()/read_iceberg() helpers injected in processors
    Any remaining extra_params keys are appended as query-string options.
    """
    from urllib.parse import urlencode, quote_plus

    extras = {k: v for k, v in read_extras(conn).items() if v not in (None, "")}
    password = decrypt(conn.password_enc) if conn.password_enc else ""

    def userinfo() -> str:
        if not conn.username:
            return ""
        return f"{quote_plus(conn.username)}{':' + quote_plus(password) if password else ''}@"

    def hostinfo(default_port: int | None = None) -> str:
        port = conn.port or default_port
        return f"{conn.host}:{port}" if port else (conn.host or "")

    def q(params: dict) -> str:
        return f"?{urlencode(params)}" if params else ""

    d = conn.dialect

    if d == "adls":
        # Storage connection — engines don't apply. get_engine() raises a
        # helpful error; processors use get_storage(name) for abfs:// paths.
        return f"adls://{conn.host}/{conn.database or ''}"

    if d == "snowflake":
        # snowflake://user:pass@account/DB/SCHEMA?warehouse=WH&role=R
        db = (conn.database or "").strip("/")
        params = {k: extras.pop(k) for k in ("warehouse", "role") if k in extras}
        params.update(extras)
        return f"snowflake://{userinfo()}{conn.host}/{db}{q(params)}"

    if d == "bigquery":
        # bigquery://project/dataset?credentials_path=/path/key.json
        params = {}
        if extras.get("credentials_path"):
            params["credentials_path"] = extras.pop("credentials_path")
        params.update(extras)
        ds = f"/{conn.database}" if conn.database else ""
        return f"bigquery://{conn.host}{ds}{q(params)}"

    if d == "clickhouse":
        proto = extras.pop("protocol", None)
        default_port = 8443 if proto == "https" else 8123
        params = ({"protocol": "https"} if proto == "https" else {})
        params.update(extras)
        db = f"/{conn.database}" if conn.database else "/default"
        return f"clickhouse+http://{userinfo()}{hostinfo(default_port)}{db}{q(params)}"

    if d == "redshift":
        db = f"/{conn.database}" if conn.database else ""
        return f"redshift+psycopg2://{userinfo()}{hostinfo(5439)}{db}{q(extras)}"

    if d == "mssql":
        driver = extras.pop("driver", "ODBC Driver 18 for SQL Server")
        params = {"driver": driver, "TrustServerCertificate": extras.pop("TrustServerCertificate", "yes")}
        params.update(extras)
        db = f"/{conn.database}" if conn.database else ""
        return f"mssql+pyodbc://{userinfo()}{hostinfo(1433)}{db}{q(params)}"

    if d == "oracle":
        # thin-mode python-oracledb: service name via query param
        params = {"service_name": conn.database or ""}
        params.update(extras)
        return f"oracle+oracledb://{userinfo()}{hostinfo(1521)}/{q(params)}"

    if d in ("starrocks", "doris"):
        # Both speak the MySQL wire protocol — no new driver needed.
        # host=FE host, port=query port (StarRocks 9030, Doris 9030), database=db
        default_port = 9030
        db = f"/{conn.database}" if conn.database else ""
        return f"mysql+pymysql://{userinfo()}{hostinfo(default_port)}{db}{q(extras)}"

    if d == "trino":
        # trino://user@host:port/catalog/schema
        # http_scheme / verify / password(→BasicAuthentication) are passed as
        # connect_args (see get_connect_args), NOT as URL query params — that's
        # what the trino sqlalchemy dialect expects.
        db = f"/{conn.database}" if conn.database else ""
        # Drop connect_arg-bound keys from the query string
        for k in ("http_scheme", "verify", "auth"):
            extras.pop(k, None)
        return f"trino://{userinfo()}{hostinfo(8080)}{db}{q(extras)}"

    if d == "databricks":
        # databricks://token:<token>@<host>?http_path=/sql/1.0/warehouses/xxx&catalog=..&schema=..
        # host=workspace host, password=access token, extras: http_path (required), catalog, schema
        token = password or ""
        params = {}
        for k in ("http_path", "catalog", "schema"):
            if extras.get(k):
                params[k] = extras.pop(k)
        params.update(extras)
        return f"databricks://token:{token}@{conn.host}{q(params)}"

    # Generic fallback (postgresql, mysql, sqlite, druid, …)
    dialect_map = {
        "postgresql": "postgresql+psycopg",
        "mysql": "mysql+pymysql",
        "sqlite": "sqlite",
        "druid": "druid",
    }
    driver = dialect_map.get(d, d)
    db = f"/{conn.database}" if conn.database else ""
    return f"{driver}://{userinfo()}{hostinfo()}{db}{q(extras)}"


def get_storage_options(conn: Connection) -> dict:
    """ADLS storage options for pandas/duckdb/fsspec abfs:// access.
    host=storage account name, password=account key (encrypted at rest),
    database=default container. extra_params may hold sas_token or
    tenant_id/client_id/client_secret for service-principal auth."""
    extras: dict = read_extras(conn)
    opts: dict = {"account_name": conn.host}
    if conn.password_enc:
        opts["account_key"] = decrypt(conn.password_enc)
    for k in ("sas_token", "tenant_id", "client_id", "client_secret"):
        if extras.get(k):
            opts[k] = extras[k]
    return opts


def get_connect_args(conn: Connection) -> dict:
    """Per-dialect connect_args that don't belong in the URL. Currently Trino:
    http_scheme (http/https), verify (TLS cert check), and BasicAuthentication
    when a username+password are set — mirroring a hand-written create_engine."""
    extras: dict = read_extras(conn)
    d = conn.dialect

    if d == "trino":
        args: dict = {}
        scheme = extras.get("http_scheme")
        if scheme:
            args["http_scheme"] = scheme
        if "verify" in extras:
            v = str(extras["verify"]).strip().lower()
            args["verify"] = v not in ("false", "0", "no", "off")
        # Password auth → BasicAuthentication(user, password)
        if conn.username and conn.password_enc:
            try:
                from trino.auth import BasicAuthentication
                args["auth"] = BasicAuthentication(conn.username, decrypt(conn.password_enc))
                # BasicAuth requires https; default the scheme if unset
                args.setdefault("http_scheme", "https")
            except Exception:
                pass
        return args

    return {}


async def list_connections(db: AsyncSession):
    r = await db.execute(select(Connection).order_by(Connection.name))
    return r.scalars().all()


async def get_connection(db: AsyncSession, conn_id: str):
    return await db.get(Connection, conn_id)


async def create_connection(db: AsyncSession, data: dict) -> Connection:
    conn = Connection(
        id=str(uuid.uuid4()),
        name=data["name"],
        dialect=data["dialect"],
        host=data.get("host"),
        port=data.get("port"),
        database=data.get("database"),
        username=data.get("username"),
        password_enc=encrypt(data["password"]) if data.get("password") else None,
        extra_params=_seal(data.get("extra_params") or {}),
    )
    if data.get("browser") or data.get("needs"):
        _set_options(conn, browser=data.get("browser") or None,
                     needs=data.get("needs") if (data.get("needs") or {}).get("password")
                     or (data.get("needs") or {}).get("extras") else None)
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


async def delete_connection(db: AsyncSession, conn_id: str) -> bool:
    r = await db.execute(delete(Connection).where(Connection.id == conn_id))
    await db.commit()
    return r.rowcount > 0


def merge_extras(conn, incoming: dict | None, clear: list[str]) -> str | None:
    """Options after an edit. The client never sees secret values, so a secret
    option it doesn't send is kept (like a blank password); one it sends with a
    value replaces the stored one; names in `clear` are removed. With incoming
    None, only `clear` applies."""
    from app.services.secret_keys import is_secret
    current = _raw_extras(conn)
    if incoming is None:
        merged = dict(current)
    else:
        merged = {k: v for k, v in current.items() if is_secret(k)}
        merged.update({k: v for k, v in incoming.items() if v not in (None, "")})
    for k in clear:
        merged.pop(k, None)
    return _seal(merged)


def _settle_needs(conn) -> None:
    """Clear what an imported connection lacked once it has been entered."""
    n = needs(conn)
    if not (n["password"] or n["extras"]):
        return
    have = _raw_extras(conn)
    left = {"password": n["password"] and not conn.password_enc,
            "extras": [k for k in n["extras"] if k not in have]}
    _set_options(conn, needs=left if (left["password"] or left["extras"]) else None)


async def update_connection(db: AsyncSession, conn_id: str, data: dict) -> Connection | None:
    conn = await db.get(Connection, conn_id)
    if not conn:
        return None
    if "name" in data:     conn.name = data["name"]
    if "dialect" in data:  conn.dialect = data["dialect"]
    if "host" in data:     conn.host = data.get("host")
    if "port" in data:     conn.port = data.get("port")
    if "database" in data: conn.database = data.get("database")
    if "username" in data: conn.username = data.get("username")
    # Only replace the password if a new non-empty one was supplied
    if data.get("password"):
        conn.password_enc = encrypt(data["password"])
    if "extra_params" in data or data.get("clear_secrets"):
        conn.extra_params = merge_extras(conn, data.get("extra_params", None), data.get("clear_secrets") or [])
    if "browser" in data:
        _set_options(conn, browser=data["browser"] or None)
    _settle_needs(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


async def test_connection(conn: Connection) -> dict:
    if needs_credentials(conn):
        return {"ok": False, "error": connection_maps([conn])["broken"][conn.name]}
    if conn.dialect == "adls":
        try:
            import adlfs
            fs = adlfs.AzureBlobFileSystem(**get_storage_options(conn))
            fs.ls(conn.database or "")
            return {"ok": True, "error": None}
        except ImportError:
            return {"ok": False, "error": "Azure Data Lake support (adlfs) isn't installed. It is in the Docker image when SFS_EXTRAS includes adls or all-connectors; the desktop app doesn't include it."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    import sqlalchemy as sa
    try:
        url = build_url(conn)
        ca = get_connect_args(conn)
    except CredentialError as exc:
        # Report the real cause instead of a 500 from the Test button
        return {"ok": False, "error": f"Connection '{conn.name}': {exc}"}
    try:
        # Some dialects reject connect_timeout — retry without it.
        # Merge dialect-specific connect_args (e.g. Trino auth/verify/scheme).
        try:
            eng = sa.create_engine(url, connect_args={**ca, "connect_timeout": 5})
            with eng.connect() as c:
                c.execute(sa.text("SELECT 1"))
        except TypeError:
            eng = sa.create_engine(url, connect_args=ca)
            with eng.connect() as c:
                c.execute(sa.text("SELECT 1"))
        eng.dispose()
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def connection_maps(conns) -> dict:
    """Build every per-connection map a run needs, isolating failures.

    A connection whose credentials can't be used (most often: SECRET_KEY changed
    since it was saved) must not break flows that never touch it. Such a
    connection gets a sentinel URL carrying the reason; the error is raised only
    if a node actually asks for it.
    """
    urls: dict[str, str] = {}
    dialects: dict[str, str] = {}
    cargs: dict[str, dict] = {}
    storage: dict[str, dict] = {}
    broken: dict[str, str] = {}
    for c in conns:
        dialects[c.name] = c.dialect
        if needs_credentials(c):
            n = needs(c)
            what = " and ".join(filter(None, ["its password" if n["password"] else "",
                                              ", ".join(n["extras"]) if n["extras"] else ""]))
            reason = (f"Connection '{c.name}' was imported without {what}. "
                      "Edit it in Meta → Connections and enter the credentials.")
            urls[c.name] = BROKEN_URL_PREFIX + reason
            broken[c.name] = reason
            continue
        try:
            urls[c.name] = build_url(c)
        except Exception as exc:
            reason = f"Connection '{c.name}' is unusable: {exc}"
            urls[c.name] = BROKEN_URL_PREFIX + reason
            broken[c.name] = reason
            continue
        try:
            cargs[c.name] = get_connect_args(c)
        except Exception:
            cargs[c.name] = {}
        if c.dialect == "adls":
            try:
                storage[c.name] = get_storage_options(c)
            except Exception as exc:
                broken[c.name] = f"Connection '{c.name}' is unusable: {exc}"
    return {"urls": urls, "dialects": dialects, "cargs": cargs,
            "storage": storage, "broken": broken}
