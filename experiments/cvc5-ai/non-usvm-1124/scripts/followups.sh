#!/usr/bin/env bash
# Post-lock audits and public panels. No new configuration choice is made here.
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
mkdir -p raw/phases
exec 8>raw/phases/followups-queue.lock
flock --nonblock 8 || exit 73
cutoff="$(date -d '2026-11-24 11:08:00+03:00' +%s)"
while [[ ! -s evidence/hw-test.complete ]]; do
    if (( $(date +%s) >= cutoff - 300 )); then
        printf '%s HW test did not reach the follow-up barrier\n' "$(date -Is)" >&2
        exit 75
    fi
    sleep 12
done
stage() {
    local name="$1" reserve="$2"
    shift 2
    if [[ -s "evidence/$name.complete" ]]; then return; fi
    if (( $(date +%s) >= cutoff - reserve )); then
        printf '%s %s budget gate\n' "$(date -Is)" "$name" >>raw/phases/followups-deferred.log
        return
    fi
    if scripts/run_phase.sh "$name" "$@"; then
        date -Is > "evidence/$name.complete.tmp"
        mv "evidence/$name.complete.tmp" "evidence/$name.complete"
    else
        printf '%s %s failure preserved\n' "$(date -Is)" "$name" >>raw/phases/followups-deferred.log
    fi
}
stage calibration 35 taskset -c 12-15 python -B scripts/calibrate.py
stage hw-wire-train 240 taskset -c 12-15 python -B scripts/hw_audit.py train --cap 25 \
    --ids schoolbook-w7-ok schoolbook-w7-bad phase-counter-w5-ok phase-counter-w5-bad sort-network-w4-ok sort-network-w4-bad
stage hw-wire-test 460 taskset -c 12-15 python -B scripts/hw_audit.py test --cap 25
stage dafny-oracle 180 taskset -c 12-15 python -B scripts/eval.py oracle dafny --cap 12
stage dafny-contracts 245 taskset -c 12-15 python -B scripts/eval.py screen dafny --cap 12 --reps 2
stage why3-transfer 250 taskset -c 12-15 python -B scripts/why3_transfer.py
stage public-hw 530 taskset -c 12-15 python -B scripts/public.py run hw
stage public-opt 880 taskset -c 12-15 python -B scripts/public.py run opt
python -B scripts/option_effects.py
python -B scripts/report.py
