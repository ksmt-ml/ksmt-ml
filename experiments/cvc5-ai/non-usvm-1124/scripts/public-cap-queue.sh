#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1
cutoff="$(date -d '2026-11-24 11:08:00+03:00' +%s)"
until compgen -G 'raw/phases/audit-followthrough-*.stop' >/dev/null; do
    if (( $(date +%s) >= cutoff - 420 )); then exit 75; fi
    sleep 9
done
if (( $(date +%s) > cutoff - 420 )); then exit 75; fi
scripts/run_phase.sh public-hw-cap45 taskset -c 12-15 python -B scripts/public_cap.py
date -Is >evidence/public-hw-cap45.complete
python -B scripts/report.py
