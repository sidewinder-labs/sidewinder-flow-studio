"""The `tm1` connection type: one form for Planning Analytics v11 (on premises)
and v12 (Planning Analytics Engine, Planning Analytics as a Service, IBM Cloud),
whose authentication differs. The `auth` field picks the mode; fields that
don't apply to it are hidden in the editor and ignored here.

TM1py does the talking (TM1Service); this module only turns the stored fields
into its keyword arguments.
"""
from __future__ import annotations

V11 = ["basic", "cam", "wia"]
SERVER = ["basic", "cam", "wia", "s2s"]          # modes addressed by host and port

AUTH_MODES = [
    {"value": "basic", "label": "v11 · TM1 security (user and password)"},
    {"value": "cam", "label": "v11 · CAM security (namespace, user, password)"},
    {"value": "wia", "label": "v11 · Windows integrated login (Windows only)"},
    {"value": "paas", "label": "v12 · Planning Analytics as a Service (API key)"},
    {"value": "ibm_cloud", "label": "v12 · IBM Cloud (IAM API key)"},
    {"value": "s2s", "label": "v12 · Planning Analytics Engine on premises / Cloud Pak (application client)"},
]


def _when(*modes):
    return {"show_when": {"auth": list(modes)}}


FIELDS = [
    {"key": "auth", "label": "Version and authentication", "type": "select", "default": "basic",
     "options": AUTH_MODES, "required": True},
    {"key": "address", "label": "Server address", "type": "string", **_when(*SERVER),
     "help": "Host name or IP of the TM1 server, without http://"},
    {"key": "port", "label": "HTTP port", "type": "number", **_when(*SERVER),
     "help": "HTTPPortNumber in tm1s.cfg (v11), or the engine's port (v12)."},
    {"key": "ssl", "label": "Use SSL", "type": "boolean", "default": True, **_when(*SERVER)},
    {"key": "user", "label": "User", "type": "string", **_when("basic", "cam", "s2s")},
    {"key": "password", "label": "Password", "type": "secret", **_when("basic", "cam")},
    {"key": "namespace", "label": "CAM namespace", "type": "string", **_when("cam"),
     "help": "The CAMNamespace ID, e.g. LDAP."},
    {"key": "base_url", "label": "Database URL", "type": "string", **_when("paas"),
     "help": "https://<region>.planninganalytics.saas.ibm.com/api/<tenant>/v0/tm1/<database>/"},
    {"key": "api_key", "label": "API key", "type": "secret", **_when("paas", "ibm_cloud")},
    {"key": "iam_url", "label": "IAM URL", "type": "string", "default": "https://iam.cloud.ibm.com", **_when("ibm_cloud")},
    {"key": "pa_url", "label": "Planning Analytics URL", "type": "string", **_when("ibm_cloud"),
     "help": "e.g. https://us-east-2.aws.planninganalytics.ibm.com"},
    {"key": "tenant", "label": "Tenant", "type": "string", **_when("ibm_cloud")},
    {"key": "instance", "label": "Instance", "type": "string", **_when("s2s")},
    {"key": "database", "label": "Database", "type": "string", **_when("ibm_cloud", "s2s")},
    {"key": "client_id", "label": "Application client ID", "type": "string", **_when("s2s")},
    {"key": "client_secret", "label": "Application client secret", "type": "secret", **_when("s2s")},
    {"key": "verify_ssl", "label": "Verify the server's certificate", "type": "boolean", "default": True,
     "help": "Turn off only for a development server with a self-signed certificate."},
]

_REQUIRED = {
    "basic": ["address", "port", "user"],
    "cam": ["address", "port", "user", "namespace"],
    "wia": ["address", "port"],
    "paas": ["base_url", "api_key"],
    "ibm_cloud": ["pa_url", "tenant", "database", "api_key"],
    "s2s": ["address", "instance", "database", "client_id", "client_secret", "user"],
}


def service_kwargs(fields: dict) -> dict:
    """Stored connection fields → TM1Service(**kwargs). Raises ValueError naming
    what's missing for the chosen mode."""
    mode = fields.get("auth") or "basic"
    if mode not in _REQUIRED:
        raise ValueError(f"Unknown TM1 authentication mode '{mode}'")
    missing = [k for k in _REQUIRED[mode] if fields.get(k) in (None, "")]
    if missing:
        labels = {f["key"]: f["label"] for f in FIELDS}
        raise ValueError(f"TM1 connection '{fields.get('name', '')}' needs: "
                         + ", ".join(labels[k] for k in missing))
    kw: dict = {"verify": bool(fields.get("verify_ssl", True)), "session_context": "Sidewinder Flow Studio"}
    if mode in SERVER:
        kw.update(address=fields["address"], ssl=bool(fields.get("ssl", True)))
        if fields.get("port") not in (None, ""):
            kw["port"] = int(fields["port"])
    if mode in ("basic", "cam"):
        kw.update(user=fields["user"], password=fields.get("password") or "")
    if mode == "cam":
        kw["namespace"] = fields["namespace"]
    if mode == "wia":
        kw["integrated_login"] = True
    if mode == "paas":
        kw.update(base_url=fields["base_url"], user="apikey", password=fields["api_key"])
    if mode == "ibm_cloud":
        kw.update(iam_url=fields.get("iam_url") or "https://iam.cloud.ibm.com", pa_url=fields["pa_url"],
                  tenant=fields["tenant"], database=fields["database"], api_key=fields["api_key"], ssl=True)
    if mode == "s2s":
        kw.update(instance=fields["instance"], database=fields["database"], user=fields["user"],
                  application_client_id=fields["client_id"],
                  application_client_secret=fields["client_secret"])
    if mode in ("paas", "ibm_cloud"):
        kw["async_requests_mode"] = True            # avoids the cloud gateway's 60 s request limit
    return kw


def connect(fields: dict):
    """An open TM1Service; use as a context manager so the session is closed."""
    from TM1py import TM1Service
    return TM1Service(**service_kwargs(fields))


def test(fields: dict) -> tuple[bool, str]:
    with connect(fields) as tm1:
        name = tm1.server.get_server_name()
        version = tm1.server.get_product_version()
    return True, f"Connected to {name} (TM1 {version})"
