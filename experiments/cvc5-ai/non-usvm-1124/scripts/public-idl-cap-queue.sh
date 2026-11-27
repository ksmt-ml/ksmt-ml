#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1
cutoff="$(date -d '2026-11-24 11:08:00+03:00' +%s)"
until compgen -G 'raw/phases/why3-live-queue-*.stop' >/dev/null; do
    if (( $(date +%s) >= cutoff - 365 )); then exit 75; fi
    sleep 8
done
if (( $(date +%s) > cutoff - 365 )); then exit 75; fi
scripts/run_phase.sh public-idl-cap30 taskset -c 12-15 python -B scripts/public_idl_cap.py
date -Is >evidence/public-idl-cap30.complete
python -B scripts/report.py
