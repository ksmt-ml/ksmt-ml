#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1
cutoff="$(date -d '2026-11-24 11:08:00+03:00' +%s)"
until compgen -G 'raw/phases/diagnostics-retry-*.stop' >/dev/null; do
    if (( $(date +%s) >= cutoff - 650 )); then exit 75; fi
    sleep 9
done
if (( $(date +%s) > cutoff - 650 )); then exit 75; fi
if [[ ! -s evidence/public-opt-retry.complete ]]; then
    scripts/run_phase.sh public-opt-retry taskset -c 12-15 python -B scripts/public.py run opt
    date -Is >evidence/public-opt-retry.complete
fi
python -B scripts/report.py
