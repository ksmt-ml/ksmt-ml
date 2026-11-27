#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export LC_ALL=C.UTF-8 TZ=Europe/Moscow
until [[ -f inputs/space.json ]]; do
    sleep 2
done
exec taskset -c 12 ../campaign/.venv/bin/python scripts/experiment.py
