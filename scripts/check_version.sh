#!/usr/bin/env bash
# Verify the version strings agree. Run before tagging a release.
# The desktop app (Tauri) needs three-part versions: 1.29 is written 1.29.0 there;
# a patch release (1.32.1) is written the same everywhere.
set -u
root="$(cd "$(dirname "$0")/.." && pwd)"

py=$(grep -m1 '^version' "$root/backend/pyproject.toml"            | grep -oE '[0-9]+\.[0-9.]+')
af=$(grep -m1 'version=' "$root/backend/app/core/app_factory.py"   | grep -oE '[0-9]+\.[0-9.]+')
pk=$(grep -m1 '"version"' "$root/frontend/package.json"            | grep -oE '[0-9]+\.[0-9.]+')
ab=$(grep -m1 'SFS_VERSION' "$root/frontend/src/components/ui/AboutDialog.tsx" | grep -oE '[0-9]+\.[0-9.]+')
tc=$(grep -m1 '"version"' "$root/desktop/src-tauri/tauri.conf.json" | grep -oE '[0-9]+\.[0-9.]+')
ct=$(grep -m1 '^version' "$root/desktop/src-tauri/Cargo.toml"       | grep -oE '[0-9]+\.[0-9.]+')

printf 'pyproject.toml    %s\napp_factory.py    %s\npackage.json      %s\nAboutDialog.tsx   %s\ntauri.conf.json   %s\nCargo.toml        %s\n' \
  "$py" "$af" "$pk" "$ab" "$tc" "$ct"

case "$py" in *.*.*) three="$py" ;; *) three="$py.0" ;; esac

if [ "$py" = "$af" ] && [ "$py" = "$pk" ] && [ "$py" = "$ab" ] && [ "$three" = "$tc" ] && [ "$three" = "$ct" ]; then
  echo "✓ all match ($py)"
else
  echo "✗ MISMATCH — fix before tagging"; exit 1
fi
