"""A small Power BI REST client: a service principal token (MSAL client
credentials) and the calls refresh needs.

The service principal needs:
  - in the Power BI admin portal, Developer settings → "Service principals can
    use Fabric APIs" (formerly "Allow service principals to use Power BI APIs")
    enabled for it, or for a security group it belongs to;
  - membership of the workspace as Member or Admin (Contributor can refresh
    too, but cannot read every model's refresh history).
No API permissions need granting in Entra ID for this.
"""
from __future__ import annotations

import time

SCOPE = "https://analysis.windows.net/powerbi/api/.default"
FIELDS = [
    {"key": "tenant_id", "label": "Tenant ID", "type": "string", "required": True,
     "help": "Directory (tenant) ID of the Entra ID tenant, a GUID."},
    {"key": "client_id", "label": "Client ID", "type": "string", "required": True,
     "help": "Application (client) ID of the service principal's app registration."},
    {"key": "client_secret", "label": "Client secret", "type": "secret", "required": True},
    {"key": "authority_host", "label": "Authority host", "type": "string",
     "default": "https://login.microsoftonline.com", "help": "Change only for a national cloud."},
    {"key": "api_base", "label": "API URL", "type": "string", "default": "https://api.powerbi.com/v1.0/myorg",
     "help": "Change only for a national cloud."},
]


class PowerBIError(RuntimeError):
    pass


def _is_guid(s: str) -> bool:
    import re
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", (s or "").strip()))


class Client:
    def __init__(self, fields: dict, *, http=None, token=None):
        import httpx
        missing = [k for k in ("tenant_id", "client_id", "client_secret") if not fields.get(k)]
        if missing:
            raise PowerBIError(f"Power BI connection '{fields.get('name', '')}' needs: {', '.join(missing)}")
        self.fields = fields
        self.base = (fields.get("api_base") or "https://api.powerbi.com/v1.0/myorg").rstrip("/")
        self.http = http or httpx.Client(timeout=60)
        self._token = token                    # tests pass a fixed token

    # ── auth ──
    def token(self) -> str:
        if self._token:
            return self._token
        import msal
        app = msal.ConfidentialClientApplication(
            self.fields["client_id"], client_credential=self.fields["client_secret"],
            authority=f"{(self.fields.get('authority_host') or 'https://login.microsoftonline.com').rstrip('/')}"
                      f"/{self.fields['tenant_id']}")
        r = app.acquire_token_for_client(scopes=[SCOPE])
        if "access_token" not in r:
            raise PowerBIError(f"Sign-in failed for the service principal: {r.get('error_description') or r.get('error')}")
        self._token = r["access_token"]
        return self._token

    def _call(self, method: str, path: str, **kw):
        for attempt in range(5):
            r = self.http.request(method, self.base + path, headers={"Authorization": f"Bearer {self.token()}"}, **kw)
            if r.status_code == 429 and attempt < 4:           # throttled: wait as told
                time.sleep(min(float(r.headers.get("Retry-After") or 5), 60))
                continue
            if r.status_code >= 400:
                raise PowerBIError(self._explain(r))
            return r
        raise PowerBIError("Power BI kept throttling the requests (HTTP 429)")

    @staticmethod
    def _explain(r) -> str:
        try:
            err = r.json().get("error", {})
            msg = err.get("message") or err.get("code") or r.text
            code = err.get("code", "")
        except Exception:
            msg, code = r.text[:300], ""
        if r.status_code == 401:
            return ("Power BI refused the service principal (401). Check that service principals may use the "
                    "Power BI APIs in the tenant settings, and that it is a member of the workspace.")
        if r.status_code == 403:
            return f"Power BI denied access (403): {msg}. The service principal must be a workspace Member or Admin."
        if r.status_code == 404:
            return f"Not found in Power BI (404): {msg}"
        return f"Power BI error {r.status_code}{f' {code}' if code else ''}: {msg}"

    # ── lookups ──
    def workspace(self, name_or_id: str) -> dict:
        """The workspace (group), by name or id, with its capacity flag."""
        name_or_id = (name_or_id or "").strip()
        if not name_or_id:
            raise PowerBIError("Name the workspace.")
        q = f"id eq '{name_or_id}'" if _is_guid(name_or_id) else f"name eq '{name_or_id.replace(chr(39), chr(39) * 2)}'"
        found = self._call("GET", "/groups", params={"$filter": q}).json().get("value", [])
        if not found:
            raise PowerBIError(f"Workspace '{name_or_id}' not found, or the service principal isn't a member of it.")
        return found[0]

    def datasets(self, workspace_id: str) -> list[dict]:
        """Every semantic model in the workspace."""
        return self._call("GET", f"/groups/{workspace_id}/datasets").json().get("value", [])

    def dataset(self, workspace_id: str, name_or_id: str) -> dict:
        name_or_id = (name_or_id or "").strip()
        if not name_or_id:
            raise PowerBIError("Name the semantic model.")
        items = self.datasets(workspace_id)
        hit = [d for d in items if d.get("id") == name_or_id] or [d for d in items if d.get("name") == name_or_id]
        if not hit:
            names = ", ".join(sorted(d.get("name", "") for d in items)[:20])
            raise PowerBIError(f"Semantic model '{name_or_id}' not found in the workspace. It has: {names or 'none'}")
        return hit[0]

    # ── refreshes ──
    def refreshes(self, ws: str, ds: str, top: int = 1) -> list[dict]:
        return self._call("GET", f"/groups/{ws}/datasets/{ds}/refreshes", params={"$top": top}).json().get("value", [])

    def start_refresh(self, ws: str, ds: str, body: dict) -> str:
        r = self._call("POST", f"/groups/{ws}/datasets/{ds}/refreshes", json=body)
        loc = r.headers.get("Location", "")
        rid = loc.rstrip("/").rsplit("/", 1)[-1] if loc else r.headers.get("RequestId") or r.headers.get("x-ms-request-id")
        if not rid:
            raise PowerBIError("Power BI accepted the refresh but returned no refresh id")
        return rid

    def refresh(self, ws: str, ds: str, refresh_id: str) -> dict:
        return self._call("GET", f"/groups/{ws}/datasets/{ds}/refreshes/{refresh_id}").json()

    def cancel(self, ws: str, ds: str, refresh_id: str) -> None:
        self._call("DELETE", f"/groups/{ws}/datasets/{ds}/refreshes/{refresh_id}")


def test(fields: dict) -> tuple[bool, str]:
    c = Client(fields)
    c.token()
    n = len(c._call("GET", "/groups", params={"$top": 5000}).json().get("value", []))
    return True, f"Signed in as the service principal; it is a member of {n} workspace{'' if n == 1 else 's'}."
