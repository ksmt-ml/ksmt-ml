#!/usr/bin/env bash
# Additional locked contrasts after the non-overlapping follow-ups.
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
shopt -s nullglob
cutoff="$(date -d '2026-11-24 11:08:00+03:00' +%s)"
until [[ $(compgen -G 'raw/phases/followups-*.stop' | wc -l) -gt 0 ]]; do
    if (( $(date +%s) >= cutoff - 180 )); then exit 75; fi
    sleep 10
done
stage() {
    local key="$1" reserve="$2"
    shift 2
    if [[ -s "evidence/$key.complete" ]]; then return; fi
    if (( $(date +%s) >= cutoff - reserve )); then
        printf '%s %s budget gate\n' "$(date -Is)" "$key" >>raw/phases/late-deferred.log
        return
    fi
    if scripts/run_phase.sh "$key" taskset -c 12-15 "$@"; then
        date -Is >"evidence/$key.complete.tmp"
        mv "evidence/$key.complete.tmp" "evidence/$key.complete"
    else
        printf '%s %s failure preserved\n' "$(date -Is)" "$key" >>raw/phases/late-deferred.log
    fi
}
stage floor-replication 220 python -B scripts/floor_replication.py
stage horizon-transfer 470 python -B scripts/horizon.py
if [[ -s raw/hw-wire-base-test-stock/rows.json ]]; then
    stage live-replay-test 340 python -B scripts/replay_sensitivity.py
fi
python -B scripts/report.py
