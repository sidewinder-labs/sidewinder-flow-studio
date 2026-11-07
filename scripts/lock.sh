#!/usr/bin/env bash
# Regenerate locks/requirements.lock: every package the Docker images, the
# desktop app and CI install, pinned for Python 3.13 on Linux, Windows and macOS.
#
#   scripts/lock.sh              re-resolve, keeping current pins where still valid
#   scripts/lock.sh --upgrade    move everything to the newest allowed versions
#
# pyproject.toml keeps version ranges (what SFS is compatible with); the lock is
# what ships. Builds install with it as a pip constraints file (-c), so any
# subset of extras gets the same pinned versions. Needs uv (pip install uv).
set -euo pipefail
cd "$(dirname "$0")/.."
uv pip compile --universal --python-version 3.13 --all-extras --quiet \
  --custom-compile-command "scripts/lock.sh" \
  backend/pyproject.toml \
  plugins/sfs-plugin-curvefit/pyproject.toml \
  plugins/sfs-plugin-sklearn/pyproject.toml \
  locks/tools.in \
  -o locks/requirements.lock "$@"
echo "locks/requirements.lock: $(grep -cE '^[a-z0-9]' locks/requirements.lock) packages"

# The report viewer is its own image (Python 3.12, its own Streamlit pin).
uv pip compile --universal --python-version 3.12 --quiet \
  --custom-compile-command "scripts/lock.sh" \
  reports-app/requirements.txt -o reports-app/requirements.lock "$@"
echo "reports-app/requirements.lock: $(grep -cE '^[a-z0-9]' reports-app/requirements.lock) packages"
