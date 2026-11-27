#!/usr/bin/env bash
# SMTBMC waits for solver exit before closing its stdin. Mirror with a daemon
# reader so the wrapper can return when the solver finishes.
set -euo pipefail
program="$(realpath "$0")"
exec /usr/bin/python3 -B "$(dirname "$program")/wire_shim.py" "$@"
