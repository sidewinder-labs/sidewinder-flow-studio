"""Public keys that sign plugin packs and the catalog, by key id. Separate
from the licence key. The private key lives only in the `plugin-signing`
GitHub Actions environment (see docs/PLUGINS.md → Signing). Add a key to
rotate; remove one only if it leaked."""

PLUGIN_KEYS: dict[str, str] = {
    "2026-11": "+QyT9Qj3B0QnR0BCO+KFTvtskF+CxhTh4WZjz6dnXGQ=",
}
