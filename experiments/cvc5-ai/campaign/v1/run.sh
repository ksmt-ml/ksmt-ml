#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
case "$1" in
  freeze) exec .venv/bin/python -B v1/phase.py freeze ;;
  smoke) exec .venv/bin/python -B v1/phase.py smoke "$2" ;;
  full) exec .venv/bin/python -B v1/phase.py full "$2" ;;
  finalize) exec .venv/bin/python -B v1/finalize.py "$2" ;;
  report) exec .venv/bin/python -B v1/report.py "$2" ;;
  *) echo "usage: v1/run.sh freeze | smoke DIR | full DIR | finalize RUN_DIR | report RUN_DIR" >&2; exit 2 ;;
esac
