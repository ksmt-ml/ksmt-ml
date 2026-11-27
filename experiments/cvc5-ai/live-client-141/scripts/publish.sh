#!/bin/bash
set -eu
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
python scripts/report.py
python scripts/check_report.py
python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0,"scripts")
from common import now
target=Path("evidence/experiment-end-msk.txt")
tmp=target.with_suffix(".tmp")
tmp.write_text(now()+"\n")
tmp.replace(target)
PY
python scripts/report.py
python scripts/check_report.py
python scripts/status.py 'Complete. Russian report and raw-run certificates verified' 'Preregistered fixed held-out design|Verified native cvc5 1.4.1 and Z3 baselines and Java/SMT smoke|Finished and audited 60 primary paired blocks|Completed interpreter-failure and exploration diagnosis|Published checked self-contained report.html' ''
