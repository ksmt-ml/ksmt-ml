#!/usr/bin/env bash
# Run the next preregistered phases once all primary Opt rounds are complete.
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
mkdir -p raw/phases
exec 8>raw/phases/continuation-queue.lock
flock --nonblock 8 || exit 73
wait_until="$(date -d '2026-11-24 09:06:00+03:00' +%s)"
while [[ ! -s raw/opt-search/search-summary.json ]]; do
    if (( $(date +%s) >= wait_until )); then
        printf '%s Opt search did not finish by queue barrier\n' "$(date -Is)" >&2
        exit 75
    fi
    sleep 12
done
stage() {
    local name="$1"
    shift
    if [[ -s "evidence/$name.complete" ]]; then return; fi
    scripts/run_phase.sh "$name" "$@"
    date -Is > "evidence/$name.complete.tmp"
    mv "evidence/$name.complete.tmp" "evidence/$name.complete"
}
publish() {
    local message="$1"
    python -B scripts/state.py "$message" \
      --done 'Версии инструментов, инвентарь и контракты' \
      --done 'TRAIN A/A: живой Yosys и статические ограничения' \
      --done 'Три равнобюджетных раунда QF_LIA завершены' \
      --next 'Проверка зафиксированных настроек и разбор сырых результатов' \
      --next 'Независимая репликация и публичные сравнения по оставшемуся времени'
    python -B scripts/report.py
}

publish 'Validation QF_LIA. Holdout остаётся закрытым'
stage opt-validation taskset -c 12-15 python -B scripts/study.py validation protocols/opt-search.json --contrast=--ite-simp
publish 'Настройки QF_LIA зафиксированы. Открытие и проверка свежих семейств'
stage opt-test taskset -c 12-15 python -B scripts/study.py test protocols/opt-search.json
publish 'Живой Yosys. Репликация последовательных BMC проектов на TRAIN'
stage hw-seq-build taskset -c 12 python -B scripts/rtl.py train --profile seq
stage hw-seq-screen taskset -c 12-15 python -B scripts/eval.py screen hw --profile seq --cap 8 --reps 2 \
    --arms 0 AA internal gauss bool prop minisat static ite repeat
stage hw-gate taskset -c 12 python -B scripts/hw_gate.py
stage hw-preflight taskset -c 12-15 python -B scripts/combo_preflight.py protocols/hw-search.json \
    sort-network-w4-ok sort-network-w4-bad --contract-profile base --cpus 12 13 14 15
publish 'Живой Yosys. Равный бюджет default, SMAC, Sol и random'
stage hw-search taskset -c 12-15 ../campaign/.venv/bin/python -B scripts/search.py protocols/hw-search.json
contrast="$(python - <<'PY'
import json
with open('evidence/hw-gate.json') as f:
    print(*json.load(f)['flags'])
PY
)"
publish 'Validation Yosys. Аппаратные тестовые входы остаются закрытыми'
stage hw-validation taskset -c 12-15 python -B scripts/study.py validation protocols/hw-search.json "--contrast=$contrast"
publish 'Настройки Yosys зафиксированы. Проверка свежих RTL'
stage hw-test taskset -c 12-15 python -B scripts/study.py test protocols/hw-search.json
publish 'Обе закрытые проверки состоялись. Продолжаются аудиты и перенос'
