#!/usr/bin/env bash
# Full Python SDK check on a Mac (or any box with python3 >= 3.10 and network).
# Runs the repo's own release check for Python: fresh venv in python/.venv,
# pip install -e ".[stream]", ruff, the whole unittest suite (C protobuf and the
# pure-Python protobuf implementation), then build + archive inspection.
# Everything is teed to ~/itm-py-check.log.
set -uo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
root="$(cd "$here/.." && pwd)"
log="$HOME/itm-py-check.log"
{
  echo "== itmatrix python check $(date -u +%FT%TZ)"
  echo "== commit $(git -C "$root" rev-parse HEAD 2>/dev/null || echo unknown) ($(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null))"
  echo "== $(python3 --version 2>&1) at $(command -v python3)"
  cd "$root" && bash scripts/check.sh python
  status=$?
  echo "== exit $status"
  exit $status
} 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
echo "log: $log"
exit "$status"
