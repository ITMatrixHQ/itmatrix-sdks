#!/usr/bin/env bash
# Export a fresh-history tree for publication.
set -euo pipefail
cd "$(dirname "$0")/.."
destination="${1:?usage: scripts/export-public.sh /absolute/new/repository}"
[[ "$destination" = /* ]] || { echo "destination must be absolute" >&2; exit 2; }
[[ ! -e "$destination" ]] || { echo "destination already exists" >&2; exit 2; }
[[ -z "$(git status --porcelain)" ]] || { echo "commit changes before export" >&2; exit 2; }
python3 scripts/check-public.py
mkdir -p "$destination"
git archive HEAD | tar -x -C "$destination"
git -C "$destination" init -b master
python3 "$destination/scripts/check-public.py"
echo "Clean tree exported without historical commits or remotes: $destination"
