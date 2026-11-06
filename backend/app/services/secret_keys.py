"""Which connection options are secrets. The one definition: storage encrypts
these, the API never returns them, and exports leave them out.

A key is secret when it contains any of SECRET_WORDS, case-insensitively, so
`sas_token`, `client_secret`, `PRIVATE_KEY` and `api_key` all count. Erring
towards secret costs only a re-entry after import (`credentials_path` is a
path, but a machine-specific one).
"""

SECRET_WORDS = ("password", "passwd", "token", "secret", "key",
                "sas_token", "client_secret", "private_key", "credentials")


def is_secret(key: str) -> bool:
    k = str(key).lower()
    return any(w in k for w in SECRET_WORDS)


def split(extras: dict | None) -> tuple[dict, dict]:
    """(non-secret, secret) parts of an options dict."""
    plain, secret = {}, {}
    for k, v in (extras or {}).items():
        (secret if is_secret(k) else plain)[k] = v
    return plain, secret
