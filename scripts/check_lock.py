"""Check that the running Python environment matches locks/requirements.lock.

Every installed distribution must be in the lock at exactly its locked
version. Exceptions: SFS's own packages, and the installer's tooling.

    python scripts/check_lock.py [path/to/requirements.lock]

Exit status 1 lists what differs. Builds run it after installing, so a
package that slipped past the lock fails the build instead of shipping.
"""
import re
import sys
from importlib import metadata
from pathlib import Path

OWN = {"flow-studio-backend"}
OWN_PREFIX = "sfs-plugin-"
TOOLING = {"pip", "setuptools", "wheel", "uv"}

# Windows consoles default to cp1252, which has no ✓/✗
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([^\s;]+)", line)
        if m:
            out[norm(m[1])] = m[2]
    return out


def main() -> int:
    lock_path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "locks" / "requirements.lock")
    lock = locked(lock_path)
    problems, n = [], 0
    for dist in metadata.distributions():
        name = norm(dist.metadata["Name"] or "")
        if not name or name in OWN or name.startswith(OWN_PREFIX) or name in TOOLING:
            continue
        n += 1
        want = lock.get(name)
        if want is None:
            problems.append(f"{name}=={dist.version} is installed but not in the lock")
        elif want != dist.version:
            problems.append(f"{name}=={dist.version} is installed; the lock says {want}")
    if problems:
        print(f"✗ environment does not match {lock_path.name}:")
        print("\n".join(f"  {p}" for p in sorted(problems)))
        print("Install with: pip install -c locks/requirements.lock ...  (scripts/lock.sh to update the lock)")
        return 1
    print(f"✓ {n} installed packages match {lock_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
