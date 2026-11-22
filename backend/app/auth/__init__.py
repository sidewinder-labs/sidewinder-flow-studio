"""Sign-in and permissions.

  permissions.py  the permission catalogue; which permission each route needs
  principal.py    who a request comes from
  providers.py    the auth interface, and which provider is in charge
  password.py     the community provider: one password for the instance
  store.py        sessions, API tokens and password hashes (shared by all providers)
  hashing.py      scrypt for passwords, SHA-256 for random secrets
  ratelimit.py    slows down password guessing
  guard.py        enforcement: routes, the WebSocket, cross-site requests
  __main__.py     server-side commands: python -m app.auth set-password
"""
