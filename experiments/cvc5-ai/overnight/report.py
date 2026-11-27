"""Render the standalone Russian report from verified evidence cards."""
from datetime import datetime
from html import escape
from timing_table import timeline
from pathlib import Path
import json
import os
import tempfile

ROOT = Path(__file__).resolve().parent
FOLLOWUP_QUEUE = (
    ("protocols/aligned-tail-finished.json", "Повторить Sol, RF и SMAC с одинаковым фоном разминки."),
    ("protocols/reset-tail-finished.json", "Измерить цену запуска процесса на коротких сеансах."),
    ("protocols/caps-tail-finished.json", "Проверить C05 при трёх лимитах библиотечного сеанса."),
    ("protocols/rewrite-tail-finished.json", "Проверить переписывание на других семействах проектов."),
    ("protocols/native-rewrite-tail-finished.json", "Сравнить переписывание на живом нативном USVM."),
    ("protocols/witness-tail-finished.json", "Проверить выбранные значения SAT-моделей в Z3."),
    ("protocols/rewrite-profile-tail-finished.json", "При повторившемся эффекте разделить время проверок и моделей."),
    ("protocols/native-budget-tail-finished.json", "Повторить живой опыт с увеличенным лимитом JVM."),
    ("protocols/warm-load-tail-finished.json", "Сравнить фон двух и шести воркеров для фиксированного набора."),
    ("protocols/simplification-tail-finished.json", "Отделить simplification=none от остальных флагов переписывания."),
    ("protocols/minimal-transfer-tail-finished.json", "При достаточном времени подтвердить минимальный вектор на независимых проектах."),
    ("protocols/paired-rewrite-tail-finished.json", "Проверить переписывание на парных формах ветвления и таблицы."),
    ("protocols/model-request-tail-finished.json", "Измерить вклад запросов моделирования при одинаковых проверках."),
    ("protocols/portable-solver-tail-finished.json", "Если хватит времени, сверить Z3 и cvc5 на одинаковых полных потоках."),
)

def atomic(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as out:
        out.write(value)
        os.fchmod(out.fileno(), 0o644)
    os.replace(out.name, path)

def write_json(path, obj):
    atomic(path, json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

def e(value):
    return escape(str(value))

def table(obj):
    headers = "".join(f'<th scope="col">{e(x)}</th>' for x in obj["columns"])
    rows = "".join("<tr>" + "".join(
        f'<th scope="row">{e(x)}</th>' if i == 0 else f'<td>{e(x)}</td>'
        for i, x in enumerate(row)) + "</tr>" for row in obj["rows"])
    return (f'<div class="scroll" tabindex="0" role="region" aria-label="{e(obj["caption"])}">'
            f'<table><caption>{e(obj["caption"])}</caption><thead><tr>{headers}</tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')

def chart(obj):
    maximum = float(obj["maximum"])
    rows = "".join(
        f'<li><span>{e(label)}</span><span class="track" aria-hidden="true">'
        f'<span style="width:{min(100,100*float(value)/maximum):.1f}%"></span></span>'
        f'<strong>{e(f"{float(value):.3f}")} {e(obj["unit"])}</strong></li>'
        for label, value in obj["values"]
    )
    return f'<figure><figcaption>{e(obj["caption"])}</figcaption><ul class="bars">{rows}</ul></figure>'

def build(current=None, next_steps=None, finished=False):
    cards = [json.loads(path.read_text()) for path in sorted((ROOT / "evidence").glob("*.json"))]
    latest = cards[-1]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    current = current or latest["summary"]
    done = [card["done"] for card in cards]
    if finished:
        next_steps = []
    elif next_steps is None:
        pending = []
        if (ROOT / "protocols/rewrite-transfer.json").exists():
            pending = [label for marker, label in FOLLOWUP_QUEUE if not (ROOT / marker).exists()]
        next_steps = list(dict.fromkeys([*pending[:3], latest["next"]]))
    sections = []
    for i, card in enumerate(cards, 1):
        body = "".join(f"<p>{e(p)}</p>" for p in card["paragraphs"])
        body += "".join(chart(c) for c in card.get("charts", []))
        body += "".join(table(t) for t in card.get("tables", []))
        body += "<ul>" + "".join(f"<li><code>{e(p)}</code></li>" for p in card["paths"]) + "</ul>"
        sections.append(f'<section id="e{i}"><h2>{e(card["title"])}</h2>{body}</section>')
    synthesis=sections.pop() if finished else ""
    schedule=timeline()
    if finished:
        schedule=schedule.replace("<td>идёт</td>", "<td>окончание не зафиксировано</td>")
    links = "".join(f'<li><a href="#e{i}">{e(c["title"])}</a></li>' for i, c in enumerate(cards, 1))
    html = f"""<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cvc5 · Ночная кампания измерений</title>
<style>
:root{{color-scheme:light;--ink:#172c36;--muted:#45606b;--line:#cfdde0;--accent:#07594f}}
*{{box-sizing:border-box}}body{{margin:0;background:#f4f8f7;color:var(--ink);font:1rem/1.55 system-ui,sans-serif}}
main{{max-width:76rem;margin:auto;padding:clamp(.9rem,3vw,3rem)}}header,section,nav{{padding:clamp(1rem,3vw,2rem);margin:0 0 1.25rem;background:white;border:1px solid var(--line);border-radius:.75rem}}
header{{border-top:.4rem solid var(--accent)}}h1,h2{{line-height:1.15;margin-top:0}}h1{{font-size:clamp(1.9rem,5vw,3rem);max-width:24ch}}h2{{font-size:clamp(1.3rem,3vw,2rem)}}
p,li{{max-width:92ch}}a{{color:var(--accent)}}code{{overflow-wrap:anywhere;color:#254b58}}small{{color:var(--muted)}}
.scroll{{overflow:auto;margin-bottom:1rem}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
caption{{text-align:left;font-weight:650;padding:.5rem 0}}th,td{{text-align:left;padding:.45rem .65rem;border-bottom:1px solid var(--line);vertical-align:top}}thead{{background:#e1eeeb}}
figure{{margin:1.25rem 0}}figcaption{{font-weight:650;margin-bottom:.5rem}}
.bars{{list-style:none;padding:0;max-width:40rem}}.bars li{{display:grid;grid-template-columns:minmax(6rem,1fr) minmax(2rem,2fr) auto;gap:.5rem;align-items:center;margin:.3rem 0}}
.track{{display:block;height:.75rem;border-radius:.375rem;background:#e1eeeb;overflow:hidden}}.track span{{display:block;height:100%;background:var(--accent);border-radius:inherit}}
tbody tr:nth-child(even){{background:#f3f7f7}}:focus-visible{{outline:.2rem solid var(--accent);outline-offset:.2rem}}
</style><main>
<header><small>butvinm-work · 22 ноября 2026 · МСК (UTC+03:00)</small>
<h1>Настройка cvc5 для инкрементальных сеансов USVM</h1>
<p><strong>{e(current)}</strong></p>
<p>Насколько устойчив победитель на исходной валидации? Есть ли запас у портфеля после учёта шума? Можно ли перенести выбор на новые семейства программ?</p>
<p><small>Обновлено <time datetime="{e(now)}">{e(now)}</time>. Крайний срок измерений 10:48 МСК, отчёта 10:55 МСК.</small></p></header>{synthesis}{schedule}
<nav aria-label="Содержание"><ul>{links}</ul></nav>
{''.join(sections)}
<section><h2>Практический вывод и следующая проверка</h2><p>{e(latest["recommendation"])}</p><p>{e(latest["next"])}</p>
<p>Пути к данным указаны относительно <code>butvinm-work:~/Dev/cvc5-ai-pilot/overnight/</code>. Машиночитаемые карточки находятся в <code>evidence/</code>.</p></section>
</main></html>"""
    atomic(ROOT / "report.html", html)
    write_json(ROOT / "current.json", {"current": current, "done": done, "next": next_steps, "updated": now})
    atomic(ROOT / "summary.txt", "\n".join([current, *done, latest["recommendation"], latest["next"], "report.html · current.json · research.log · protocols/ · evidence/ · runs/"]) + "\n")

if __name__ == "__main__":
    build()
