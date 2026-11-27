#!/bin/bash
set -eu
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
while ! rg -q '"event": "campaign_end"' runs/primary/supervisor.jsonl; do
    if [[ "$(date +%s)" -ge "$(date -d '2026-11-22T19:43:00+03:00' +%s)" ]]; then
        date -Iseconds
        echo 'Primary campaign did not close before the finalization deadline.'
        exit 3
    fi
    sleep 8
done
date -Iseconds
python scripts/status.py 'Reconstructing primary outcomes and verifying raw JVM evidence' 'Read overnight reports, protocols and evidence 17, 25b and 31|Preregistered and froze 80 held-out methods in 10 independent projects|Passed JNI and live Java smoke checks|Primary 60-block acquisition closed' 'Audit primary data|Apply the supplementary diagnostic trigger|Write and verify Russian report'
scripts/finish_primary.sh
date -Iseconds
python scripts/status.py 'Evaluating the trigger for the supplementary interpreter-policy check' 'Primary acquisition and independent raw-data audit complete|Confirmed runtime identities and primary project-cluster estimates' 'Diagnose USVM interpreter failures|Write and verify Russian report'
python -u scripts/stock.py
if [[ -f runs/stock/derived-schedule.json ]]; then
    python scripts/analyze.py stock
    python scripts/stock_verify.py
fi
date -Iseconds
python scripts/status.py 'Writing and verifying the standalone report' 'Primary acquisition and independent raw-data audit complete|Interpreter policy diagnostics complete|Runtime identities, coverage and cluster analysis reconciled' 'Publish verified Russian report'
