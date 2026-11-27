#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/usr/bin:/bin"
case "${1:-}" in
  smoke)
    out="${2:?Provide a new runs/smoke path}"
    mkdir -p runs
    exec ../campaign/.venv/bin/python -B engine/interactive.py "$out" --seed 91573 --core 9 --smoke \
      --begin "$(python3 -c 'import time; print(time.monotonic())')"
    ;;
  full)
    exec ../campaign/.venv/bin/python -B engine/manage.py "${2:?Provide a new runs output path}" \
      --smoke "${3:?Provide a completed runs/smoke path}"
    ;;
  *)
    echo "Usage: $0 smoke runs/NEW-SMOKE | full runs/NEW-EXPERIMENT runs/PASSED-SMOKE" >&2
    exit 2
    ;;
esac
