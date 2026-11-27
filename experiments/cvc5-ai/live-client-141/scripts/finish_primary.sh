#!/bin/bash
set -eu
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
rg -q 'campaign_end' runs/primary/supervisor.jsonl
python scripts/analyze.py primary
python scripts/diagnostics.py
python scripts/verify.py primary
