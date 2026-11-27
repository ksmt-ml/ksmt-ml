from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from html import escape
import re
import os
import tempfile

root = Path(__file__).resolve().parent
zone = ZoneInfo("Europe/Moscow")
events = [
    ("PIPELINE ", "этап", None), ("COMPLETED ", "этап", "завершён"), ("FAILED ", "этап", "сбой"),
    ("RECOVERY START ", "повтор", None), ("RECOVERY OK ", "повтор", "завершён"), ("RECOVERY FAIL ", "повтор", "сбой"),
    ("START materialize ", "подготовка корпуса", None), ("START runs/", "измерение", None),
    ("VERIFY runs/", "измерение", "проверено"), ("VERIFY ", "подготовка корпуса", "проверено")
]
labels = {"01-stability":"Устойчивость выбора финалистов", "02-portfolio/dev-replay":"Портфель · выбор настроек", "02-portfolio/test-replay":"Портфель · закрытая проверка", "04-transfer/replay":"Перенос на библиотечные методы", "06-cap-calibration/replay":"Влияние таймаута", "08-prefix/replay":"Выбор по префиксу сессии"}
def timeline():
    rows = []
    active = {}
    for line in (root/"research.log").read_text().splitlines():
        stamp, _, msg = line.partition(" ")
        try:
            stamp = datetime.fromisoformat(stamp).astimezone(zone).strftime("%d.%m %H:%M:%S")
        except ValueError:
            continue
        for tag, kind, result in events:
            if not msg.startswith(tag):
                continue
            name = re.split(r"[,:\s]", msg[len(tag):], maxsplit=1)[0]
            key = kind, name
            if not result:
                active[key] = len(rows)
                rows.append([labels.get(name,name),kind,stamp,"идёт"])
            elif key in active:
                rows[active.pop(key)][3] = stamp + " · " + result
            break
    # A failed parent stage may have interrupted a nested operation.
    for (kind,name), i in active.items():
        if kind == "подготовка корпуса" and any(r[3].endswith("сбой") and r[2]<=rows[i][2]<=r[3][:14] for r in rows):
            rows[i][3]="окончание не зафиксировано"
    if (root / "campaign-verification.json").exists():
        for row in rows:
            if row[3] == "идёт":
                row[3] = "окончание не зафиксировано"
    return ('<!-- timing-start --><section id="timing"><h2>Начало и окончание экспериментов</h2><details><summary>Расписание прогонов по МСК</summary><div class="scroll"><table><caption>По журналу запуска и проверки · московское время (UTC+3)</caption><thead><tr><th>Эксперимент</th><th>Этап</th><th>Начало, МСК</th><th>Окончание, МСК</th></tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div></details></section><!-- timing-end -->')
def refresh():
    path = root/"report.html"
    html = path.read_text()
    schedule = timeline()
    if '<!-- timing-start -->' in html:
        new = re.sub(r'<!-- timing-start -->.*?<!-- timing-end -->', lambda _: schedule, html, count=1, flags=re.S)
    else:
        new = html.replace('</header>', '</header>' + schedule, 1)
    if html != new:
        with tempfile.NamedTemporaryFile(mode='w',dir=root,delete=False) as out:
            out.write(new)
            os.fchmod(out.fileno(),0o644)
        os.replace(out.name,path)
if __name__ == "__main__":
    refresh()
