#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
export TZ=Europe/Moscow LC_ALL=C.UTF-8
taskset -c 12 python scripts/verify_live.py
taskset -c 12 python scripts/report_live.py
taskset -c 12 python scripts/check_report.py
python - <<'PY'
import sys
sys.path.insert(0,"scripts")
from base import ROOT, now, read, status
v=read(ROOT/"evidence/verification.json")
(ROOT/"evidence/session-end-msk.txt").write_text(now()+"\n")
status("Эксперимент завершён. Отчёт и сырые данные сохранены.",
       ["Восстановлены исторические знаменатели и причины отказов",
        "Выполнены поиск SMAC и Sol и заморозка перед закрытым тестом",
        f"Независимо сверено {v['verified_jvms']} JVM",
        "Опубликованы report.html и summary.txt"],[])
PY
