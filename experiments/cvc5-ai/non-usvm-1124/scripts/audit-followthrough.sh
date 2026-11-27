#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1
cutoff="$(date -d '2026-11-24 11:08:00+03:00' +%s)"
until compgen -G 'raw/phases/public-retry-queue-*.stop' >/dev/null; do
    if (( $(date +%s) >= cutoff - 140 )); then exit 75; fi
    sleep 9
done
if (( $(date +%s) < cutoff - 100 )); then
    scripts/run_phase.sh karatsuba-oracle taskset -c 12 python -B scripts/karatsuba_oracle.py
    date -Is >evidence/karatsuba-oracle.complete
fi
if (( $(date +%s) < cutoff - 170 )); then
    readarray -t targets < <(python - <<'PY'
import json
from pathlib import Path
p=Path('raw/hw-wire-base-test-stock/rows.json')
for r in json.loads(p.read_text()):
    if not r.get('same_answers',False):
        print(r['id'])
PY
)
    if (( ${#targets[@]} )); then
        scripts/run_phase.sh hw-wire-fixed taskset -c 12-15 python -B scripts/hw_audit.py test \
            --cap 25 --flag=--bv-solver=bitblast-internal --tag fixed --ids "${targets[@]}"
        date -Is >evidence/hw-wire-fixed.complete
    fi
fi
python -B scripts/report.py
