#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1
cutoff="$(date -d '2026-11-24 11:08:00+03:00' +%s)"
until compgen -G 'raw/phases/public-idl-final-queue-*.stop' >/dev/null; do
    if (( $(date +%s) >= cutoff - 150 )); then exit 75; fi
    sleep 9
done
if (( $(date +%s) < cutoff - 110 )); then
    if scripts/run_phase.sh karatsuba-oracle-retry taskset -c 12 python -B scripts/karatsuba_oracle.py; then
        date -Is >evidence/karatsuba-oracle.complete
    fi
fi
if (( $(date +%s) < cutoff - 170 )); then
    readarray -t ids < <(jq -r '.[]|select(.same_answers!=true)|.id' raw/hw-wire-base-test-stock/rows.json)
    if scripts/run_phase.sh hw-wire-fixed-retry taskset -c 12-15 python -B scripts/hw_audit.py test \
        --cap 25 --flag=--bv-solver=bitblast-internal --tag fixed --ids "${ids[@]}"; then
        date -Is >evidence/hw-wire-fixed.complete
    fi
fi
python -B scripts/report.py
