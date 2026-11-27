#!/usr/bin/env bash
# Retry the diagnostic producers after the primary and transfer lanes finish.
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1
cutoff="$(date -d '2026-11-24 11:08:00+03:00' +%s)"
until compgen -G 'raw/phases/late-*.stop' >/dev/null; do
    if (( $(date +%s) >= cutoff - 220 )); then exit 75; fi
    sleep 9
done
if (( $(date +%s) > cutoff - 220 )); then exit 75; fi
if [[ ! -s evidence/why3-transfer-retry.complete ]]; then
    scripts/run_phase.sh why3-transfer-retry taskset -c 12-15 python -B scripts/why3_transfer.py
    date -Is >evidence/why3-transfer-retry.complete
fi
if [[ ! -s evidence/dafny-contracts-v2.complete ]]; then
    scripts/run_phase.sh dafny-oracle-v2 taskset -c 12-15 python -B scripts/eval.py oracle dafny --cap 12 --label batchcount-v2
    scripts/run_phase.sh dafny-contracts-v2 taskset -c 12-15 python -B scripts/eval.py screen dafny --cap 12 --reps 2 --label batchcount-v2
    date -Is >evidence/dafny-contracts-v2.complete
fi
python -B scripts/report.py
