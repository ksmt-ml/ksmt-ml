"""Update the operator journal and atomic Markdown/JSON status."""
import json
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
END = "2026-11-23 09:26:11 МСК"
STOP = "2026-11-23 09:21:00 МСК"

def now():
    return datetime.now(ZoneInfo("Europe/Moscow")).isoformat(timespec="seconds")

def atomic(path, content):
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="."+path.name+"-", delete=False) as stream:
            tmp = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)

def status(key, current, finished, next_steps):
    logfile = ROOT/"evidence/timeline.jsonl"
    history = [json.loads(s) for s in logfile.read_text().splitlines()] if logfile.exists() else []
    at = now()
    if key not in {p["stage"] for p in history}:
        history.append(dict(stage=key,at=at,message=current,completed=finished))
        atomic(logfile,"".join(json.dumps(row,ensure_ascii=False)+"\n" for row in history))
    done = [r["completed"] for r in history if r["completed"]]
    atomic(ROOT/"current.json",json.dumps(dict(current=current,done=done,next=next_steps,updated=at),
                                          ensure_ascii=False,indent=2)+"\n")
    text = [
        "# Контрольная точка кампании",
        "",
        f"Обновлено {at}. Хост butvinm-work. Сбор до {STOP}, отчёт до {END}.",
        "",
        current,
        "",
        "## Выполнено",
        "",
        *[f"- {r['at']} — {r['completed']}" for r in history if r["completed"]],
        "",
        "## Следующие действия",
        "",
        *[f"- {step}" for step in next_steps],
        "",
        "## Продолжение",
        "",
        "- Проверить часы командой `TZ=Europe/Moscow date -Is`, свободное место и активные процессы на ядрах 12–15.",
        "- Не повторять проверенные цельные блоки. Сверить recovery/evidence и followup/evidence перед запуском.",
        "- Исходные кампании читать без записи. Все новые логи остаются в каталоге кампании.",
        "- Последняя команда отчёта: `PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python scripts/render.py`.",
        "",
    ]
    atomic(ROOT/"CHECKPOINT.md","\n".join(text))

if __name__=="__main__":
    key, current, finished, *next_steps = sys.argv[1:]
    status(key,current,finished,next_steps)
