import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(__file__).resolve().parents[1]
path = root / "current.json"
now = datetime.now(ZoneInfo("Europe/Moscow")).isoformat()
current, done, next_steps = sys.argv[1:]
status = dict(current=current, done=done.split("|") if done else [],
              next=next_steps.split("|") if next_steps else [], updated=now)
temp = path.with_suffix(".tmp")
temp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
os.replace(temp, path)
with (root / "evidence/timeline.jsonl").open("a") as f:
    f.write(json.dumps(status, ensure_ascii=False) + "\n")
