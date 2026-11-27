#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1
cutoff="$(date -d '2026-11-24 11:08:00+03:00' +%s)"
until compgen -G 'raw/phases/public-idl-cap-queue-*.stop' >/dev/null; do
    if (( $(date +%s) >= cutoff - 300 )); then exit 75; fi
    sleep 8
done
if (( $(date +%s) > cutoff - 300 )); then exit 75; fi
scripts/run_phase.sh public-hw-oracle110 taskset -c 12-15 python -B scripts/public_hw_oracle.py
date -Is >evidence/public-hw-oracle110.complete
python -B scripts/report.py
