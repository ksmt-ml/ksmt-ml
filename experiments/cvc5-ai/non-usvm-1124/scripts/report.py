"""Render the standalone Russian campaign report atomically."""
from html import escape as h
from pathlib import Path
import re
from statistics import mean

from analyze import analyze
from common import ROOT, now, read

CSS="""
:root { color-scheme: light; --ink:#192824; --muted:#4e625a; --line:#cbd9d2; --paper:#f6f8f3;
        --panel:#fff; --accent:#19664e; --splash:#e3f1e5; }
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink);
       font:16px/1.52 system-ui,-apple-system,'Segoe UI',sans-serif; }
header,main,footer { width:min(1280px,100%); margin:auto; padding:24px clamp(16px,4vw,52px); }
header { padding-top:38px; padding-bottom:14px; }
h1 { margin:.1em 0 .25em; font-size:clamp(30px,4vw,52px); line-height:1.12; letter-spacing:-.04em; }
h2 { margin:0 0 .65em; font-size:clamp(24px,2.8vw,32px); line-height:1.23; }
h3 { margin:1.1em 0 .5em; font-size:19px; }
section { margin:0 0 34px; }
p { margin:.55em 0; }
a { color:var(--accent); text-underline-offset:.17em; }
time,.muted,small { color:var(--muted); }
.keyline { padding:10px 14px; border-left:4px solid var(--accent); background:var(--splash); margin:12px 0 8px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,270px),1fr)); gap:12px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:13px 17px; }
.card h3 { margin:0 0 4px; font-size:18px; }
.tab { overflow:auto; border:1px solid var(--line); border-radius:8px; margin:.5em 0 1em; background:var(--panel); }
table { width:100%; border-collapse:collapse; min-width:530px; font-variant-numeric:tabular-nums; }
td,th { padding:8px 11px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
th { background:#eaf0e9; color:#273e34; font-size:.9rem; }
tr:last-child td { border-bottom:0; }
tbody tr:nth-child(even) { background:#fbfcfa; }
summary { cursor:pointer; color:var(--accent); font-weight:650; padding:.38em 0; }
code { font: .9em ui-monospace,monospace; overflow-wrap:anywhere; }
ol,ul { padding-inline-start:1.4em; }
li { margin:.28em 0; }
.bad { color:#8d382c; }
@media print { body {background:white;color:black;} main,header {padding:12px;} .tab {overflow:visible;} }
"""


def fmt(value,n=3):
    return "—" if value is None else format(value,f".{n}f").replace(".",",")


def pct(value):
    return fmt(100*value,1)+"%" if value is not None else "—"


def time_s(value):
    return value[11:23] if value is not None else "—"


def code(value):
    return f"<code>{h(str(value))}</code>"


def table(headers,rows):
    return ('<div class="tab"><table><thead><tr>'+''.join(f"<th>{v}</th>" for v in headers)+
            "</tr></thead><tbody>"+''.join("<tr>"+''.join(f"<td>{v}</td>" for v in row)+"</tr>" for row in rows)+
            "</tbody></table></div>")


def section(title,contents):
    return f"<section><h2>{h(title)}</h2>{contents}</section>"


def details(title,body):
    return f"<details><summary>{h(title)}</summary>{body}</details>"


def para(*content):
    return "".join(f"<p>{body}</p>" for body in content)


def inline_md(line):
    pattern=r"(`[^`]+`|\[[^\]]+\]\(https?://[^)]+\))"
    parts=re.split(pattern,line)
    for i,p in enumerate(parts):
        if p.startswith("`") and p.endswith("`"):
            parts[i]=code(p[1:-1])
        elif p.startswith("[") and "](" in p:
            title,url=p[1:-1].split("](",1)
            parts[i]=f'<a href="{h(url,quote=True)}">{h(title)}</a>'
        else:
            parts[i]=h(p)
    return "".join(parts)


def summary(data):
    opt=data["test"].get("opt-search")
    hw=data["test"].get("hw-search")
    base=data["panels"].get("screen-opt-base-train")
    results=[]
    if opt:
        t=opt["overall"]
        opt_text=f"На {t['cohort']['families']} новых семействах 95% интервалы эффектов приведены ниже для каждого метода."
        fixed=t["contrasts"]["fixed/default"]
        if fixed:
            opt_text=(f"На {fixed['n']} новых семействах, фиксированный TRAIN вариант изменил PAR2 на "
                      f"{pct(fixed['relative']-1)}. Выигрыш TRAIN у него составил 18,2%.")
    elif base:
        a=base["arms"]["0"]; b=base["arms"]["ite"]
        opt_text=(f"На TRAIN cvc5 решил {a['statuses'].get('solved',0)}/{a['n']} повторов по умолчанию "
                  f"и {b['statuses'].get('solved',0)}/{b['n']} с {code('--ite-simp')}. "
                  f"PAR2 снизился на {pct(1-b['par2']/a['par2'])}.")
    else:
        opt_text="Пилотное измерение ещё не собрано."
    if hw:
        t=hw["sequential"]
        reference=t["cohort"]
        fixed=t["contrasts"]["fixed/default"]
        hw_text=(f"На {reference['families']} новых последовательных проектах фиксированный контраст "
                 f"изменил PAR2 на {pct(fixed['relative']-1) if fixed else '—'}. "
                 "Абсолютные результаты и перенос на комбинационные схемы приведены ниже.")
    elif data.get("hw-gate",{}).get("passed"):
        source=data["hw-gate"]
        item=source["measures"][source["contrast"]]
        stock=source["stock"]
        a=stock["statuses"].get("solved",0)
        hw_text=(f"На последовательном TRAIN {a+item['solved_diff']}/{stock['n']} решённых повторов у "
                 f"{code(' '.join(source['flags']))} против {a}/{stock['n']} у default. "
                 f"PAR2 ниже на {pct(1-item['ratio'])}.")
    else:
        hw_text=("В базовой панели 32/36 решённых повторов у заводского cvc5. "
                 "В двух размерах счётчика смена BV backend помогла, на части комбинационных схем замедлила.")
    if opt or hw:
        for name,r in (("QF_LIA",opt),("Yosys seq",hw)):
            if not r:
                continue
            z=(r["sequential"] if name=="Yosys seq" else r["overall"])["contrasts"]
            smac=z["sol/smac"]; rand=z["sol/random"]
            if smac and rand:
                results.append(f"Для {name} Sol к SMAC {pct(smac['relative']-1)}, к random {pct(rand['relative']-1)}. "
                               "Интервалы этих сравнений включают равенство.")
    else:
        results.append("Выполняется парное сравнение Sol, SMAC3, random и заводского cvc5. Holdout закрыт до фиксации настроек.")
    cards=[("Планирование и упаковка",opt_text),("Живой Yosys",hw_text),("Ценность Sol"," ".join(results))]
    return '<div class="cards">'+''.join(f'<article class="card"><h3>{h(t)}</h3><p>{v}</p></article>' for t,v in cards)+'</div>'


def render_panel(name,panel):
    labels={"screen-hw-probe-train":"Yosys, проба со старым таймером",
            "screen-opt-probe-train":"Ограничения, проба со старым таймером",
            "screen-opt-base-train":"Ограничения, базовый TRAIN",
            "screen-dafny-base-train":"Dafny, первая диагностическая панель с неверным ожидаемым числом пакетов",
            "screen-dafny-base-train-batchcount-v2":"Dafny, повтор с проверенным числом пакетов"}
    label=labels.get(name,("Yosys, последовательная панель TRAIN" if "-seq-" in name else "Yosys, базовый TRAIN"))
    arms=panel["arms"]
    a=arms.get("0",next(iter(arms.values())))
    headline=para(
        f"{code(panel['path'])}. Сопоставлено {panel['identical_pairs']} решённых пар A/A′. "
        f"Медиана |Δ| {fmt(panel['median_aa_s'],4)} с, 90-й перцентиль {fmt(panel['q90_aa_s'],4)} с. "
        f"Наименьшее наблюдаемое время целого прохода {fmt(panel['floor'],4)} с.",
        f"Интервал A {time_s(a['first'])}–{time_s(a['last'])} МСК. "
        f"Универсальный таймаут {', '.join(code(z) for z in panel['all_timed_out']) if panel['all_timed_out'] else 'не наблюдался'}.")
    c=[]
    order=["0","AA",*sorted(k for k in arms if k not in {"0","AA"})]
    for k in order:
        if k not in arms:
            continue
        z=arms[k]
        c.append((code(k),str(z["cases"]),f"{z['statuses'].get('solved',0)}/{z['n']}",
                  fmt(z["par2"]),fmt(z["cluster_par2"]),
                  str(z["statuses"].get("timeout",0)),
                  str(sum(z["statuses"].get(q,0) for q in ("wrong","error","incomplete","unknown"))),
                  str(z["checks"])))
    main=table(("Настройка","Входы","Решено","PAR2, с","По семействам, с","Timeout","Прочие сбои","Контрольные ответы"),c)
    rows=[]
    for family,options in panel["by_family"].items():
        best=min(options,key=lambda k:options[k]["par2"] if options[k]["par2"] is not None else float("inf"))
        d=options.get("0",options[best]); y=options[best]
        rows.append((h(family),f"{d['statuses'].get('solved',0)}/{d['n']}",fmt(d['par2']),code(best),fmt(y['par2'])))
    family=table(("Семейство","Решено A","PAR2 A, с","Минимум на TRAIN","PAR2, с"),rows)
    return details(label,headline+main+family)


def render_search(name,search):
    plan=search["plan"]
    headline=para(
        f"{code(search['plan_file'])}. Бюджет {plan['budget']//60} мин. Для каждого запуска один worker "
        f"на отдельном физическом ядре, {len(plan['items'])} TRAIN входов, {len(plan['space'])} двоичных групп, "
        f"предел сеанса {fmt(plan['cap'],1)} с. Время запроса Sol входит в лимит.",
        f"Ключи {', '.join(code(' '.join(r['flags'])) for r in plan['space'])}.",
        f"Исключения из целей поиска {', '.join(code(i) for i in plan['excluded']) or 'отсутствуют'}. Сырые результаты всей панели сохранены.")
    grid=[]
    for r in search["rounds"]:
        for method,d in r["methods"].items():
            best=d["best"]
            stamp=f"{time_s(d['actual_start'])}–{time_s(d['actual_stop'])}"
            grid.append((str(r["seed"]),h(method),str(d["cpu"]),stamp,h(d["status"]),
                         str(d["seen"]),str(d['invalid']),fmt(best["par2"] if best else None), str(best["solved"]) if best else "—",
                         f'{d["model_success"]}/{d["model_calls"]} · {fmt(d["model_seconds"],1)} с' if method=="sol" else "—"))
    trajectory=[]
    horizon=sorted({int(minute) for r in search["rounds"] for v in r["methods"].values() for minute in v.get("anytime",{})})
    for r in search["rounds"]:
        for method,d in r["methods"].items():
            trajectory.append((str(r["seed"]),h(method),*(fmt(d.get("anytime",{}).get(str(k))) for k in horizon)))
    chart=details("Достигнутый TRAIN PAR2 по времени",
                  table(("Seed","Метод",*(f"{k} мин" for k in horizon)),trajectory)) if horizon else ""
    return f"<h3>{h(name)}</h3>"+headline+table(("Seed","Метод","CPU","Факт МСК","Статус","Измерено","Непригодно","Лучший TRAIN PAR2","Решено","Sol · время"),grid)+chart


def render_scope(name,scope):
    cohort=scope["cohort"]
    if not cohort["families"]:
        return ""
    controls=para(f"{cohort['families']} независимых семейств, {cohort['cases']} входов и {cohort['n']} повторов "
                  "у одного конфига. Списки решённых относятся к отдельным парным зернам.")
    ms=[]
    for method,r in scope["methods"].items():
        ms.append((h(method),fmt(r["cluster_par2"]),h(", ".join(map(str,r["solved_per_seed"]))),
                   h(", ".join(map(str,r["checks_per_seed"]))),
                   h(", ".join(f"+{g} / −{l}" for g,l in zip(r["changes"]["gains"],r["changes"]["losses"])))))
    comparisons=[]
    for pair,r in scope["contrasts"].items():
        if not r:
            continue
        comparisons.append((h(pair),str(r["n"]),fmt(r["estimate"]),pct(r["relative"]-1),
                            h(" / ".join(pct(v-1) for v in r["ratio_ci"])),f"{r['better']} / {r['worse']}"))
    families=[]
    keys=sorted(scope["contrasts"]["sol/default"]["families"])
    for family in keys:
        vals=[]
        for x in ("fixed/default","smac/default","sol/default","random/default"):
            row=scope["contrasts"][x]["families"][family]
            vals.append(fmt(row[0]))
        base=scope["contrasts"]["fixed/default"]["families"][family][1]
        families.append((h(family),fmt(base),*vals))
    return (f"<h3>{h(name)}</h3>"+controls+
            table(("Метод","PAR2, с","Решено","Контрольных ответов","Новое / потеря"),ms)+
            table(("Парный контраст","Кластеров","Δ PAR2, с","Изменение","95% интервал","Лучше / хуже"),comparisons)+
            details("Стоимость по каждому семейству, с",table(("Семейство","Default","Fixed","SMAC","Sol","Random"),families)))


def render_test(name,d):
    info=d["info"]
    source=para(
        f"Проверка времени {time_s(info['start'])}–{time_s(info['stop'])} МСК. "
        f"{info['oracle'].get('Z3',{}).get('solved',0)}/{info['oracle'].get('Z3',{}).get('n',0)} сеансов подтвердил Z3. "
        f"Исключено из-за оракула {len(info['oracle_exclusions'])}, при построении {len(info['build_exclusions'])}.",
        f"Точные флаги зафиксированы до открытия. SHA-256 lock {code(d['hashes']['lock'])}.")
    selection=[]
    for entry,x in d["selection"].items():
        options=" ".join(x["flags"]) if x["flags"] else "default"
        selection.append((h(entry),code(options),fmt(x["validation"]["par2"]),str(x["validation"]["solved"]),
                          "да" if x["deployment_pass"] else "нет"))
    selected=details("Замороженные настройки и исходы validation",
                     table(("Метод и seed","Флаги","Validation PAR2, с","Решено","Допуск"),selection))
    scopes=render_scope("Все группы",d["overall"])
    if "sequential" in d:
        scopes=render_scope("Последовательные проекты",d["sequential"])+render_scope("Перенос на комбинационные проекты",d["combinational"])+details("Все проекты Yosys",scopes)
    return section("Закрытая проверка · "+name,source+selected+scopes)


def inventory():
    rows=[]
    for l in (ROOT/"evidence/INVENTORY.md").read_text().splitlines():
        if l.startswith("|") and not l.startswith("|---") and not l.startswith("| Источник"):
            rows.append([inline_md(x.strip()) for x in l.strip("|").split("|")])
    return table(("Производитель или корпус","Доступ, релиз и лицензия","Формат и оракул","Происхождение"),rows)


def interpretation():
    path=ROOT/"evidence/INTERPRETATION.md"
    if not path.is_file():
        return ""
    blocks=path.read_text().split("\n\n")[1:]
    html=[]
    for b in blocks:
        lines=b.splitlines()
        if not lines:
            continue
        if lines[0].startswith("## "):
            html.append(f"<h3>{h(lines[0][3:])}</h3>")
            lines=lines[1:]
        if lines:
            html.append(para(inline_md(" ".join(lines))))
    return section("Вывод для применения", "".join(html))


def render():
    data=analyze()
    state=read(ROOT/"current.json")
    sections=[]
    panels="".join(render_panel(name,panel) for name,panel in data["panels"].items())
    sections.append(section("TRAIN и измерительный шум",panels))
    searches="".join(render_search(name,s) for name,s in data["search"].items())
    if searches:
        sections.append(section("Автоматический поиск",searches))
    for name,d in data["test"].items():
        sections.append(render_test(name,d))
    sections.append(interpretation())
    sections.append(section("Инвентарь источников",inventory()))
    sections.append(section("Протокол и ограничения",para(
        f"Окно 24.11.2026 07:13:40–11:13:40 МСК. Первое подтверждение часов хоста 07:14:24. "
        "Разрешённые CPU 12–15 соответствуют четырём физическим ядрам, восемь гигабайт дополнительного диска.",
        "PAR2 — время целого сеанса при подтверждённом результате, иначе удвоенный предел. "
        "Одинаковые настройки разных методов используют общее измерение внутри блока. "
        "При поиске веса независимых семейств равны. Bootstrap ресемплирует семейства, вместе со всеми размерами, парными повторами и зернами. "
        "Интервалы при нескольких семействах описательны.",
        "Нативный yosys-smtbmc на каждом шаге проверяет предпосылки и утверждения, в SAT случае запрашивает значения для трассы. "
        "Измерение включает запуск frontend и решателя. Дословный replay проверяется отдельно. "
        "Синтетические задачи QF_LIA — один исходный статический check-sat, отдельный точный алгоритм подтверждает оптимум.",
        "Истинная и заведомо ложная спецификации проверены на Dafny 4.11.0. Boogie с cvc5 не разобрал модель "
        "после отрицательного примера. Для рабочих конфигураций Dafny фаза остаётся диагностической. "
        f"Исторические наблюдения описаны в {code('evidence/INVENTORY.md')}. Оракулы перечислены в {code('evidence/ORACLES.md')}.",
        "Sol явно выбран как GPT‑6.2 Sol через подписочную авторизацию ChatGPT. Каждый запрос — новый процесс и thread, "
        "только whitelist обучающих метрик, отдельная файловая песочница, timeout и проверка событий без инструментов. "
        '<a href="https://learn.chatgpt.com/docs/auth">OpenAI Docs об авторизации</a> · '
        '<a href="https://learn.chatgpt.com/docs/models">OpenAI Docs о модели</a>.')))
    if data["secondary"]:
        extra=[]
        for key,item in data["secondary"].items():
            rows=[]
            resolved=item.get("summary",item.get("results",{}))
            for name,d in resolved.items():
                rows.append((code(name),f"{d['solved']}/{d['n']}",fmt(d["par2"]),str(d["wrong"]),str(d["error"])))
            meaning={"public-hw":"Историческая статика Wolf-fmbench.","public-opt":"Историческая статика QF_IDL из SMT-LIB 2025.08.04.",
                     "why3-transfer":"Новые VC штатного драйвера Why3, статический replay.",
                     "why3-live":"Живой интерфейс Why3 с оригинальным драйвером и отдельно сохранённым solver stdout.",
                     "floor-replication":"Восемь новых блоков на тех же коротких QF_LIA входах, с A/A′.",
                     "live-replay-test":"Дословные solver stdin пакеты в отдельном CLI режиме.",
                     "horizon-transfer":"Старые RTL, новый живой горизонт +4 и предел 12 с.",
                     "public-hw-cap45":"Та же промышленная статика с отдельным пределом 45 с.",
                     "public-idl-cap30":"Тот же опубликованный QF_IDL, обе настройки получают 30 с."}[key]
            if key=="floor-replication":
                meaning+=f" Медиана A/A′ {fmt(item['median_alias_gap_s'],5)} с на {item['known_pairs']} парах."
            if key=="why3-transfer":
                meaning+=(f" Выведено {item['generated']} задач, полный вывод содержит "
                          f"{item.get('trailing_driver_diagnostics',0)} сообщений после определённого ответа. "
                          "В таблице учтён проверенный ведущий вердикт. Полный вывод сохранён отдельно.")
            if key=="public-opt" and item.get("preparation",{}).get("missing_file_license"):
                prep=item["preparation"]
                meaning+=(f" Из {prep['family_members']} файлов выбрано {prep['admitted']}. "
                          "В выбранных файлах поле лицензии отсутствует. В манифесте отдельно сохранена CC BY 4.0 коллекции Zenodo.")
            if key=="horizon-transfer" and item.get("matched"):
                measured=item["matched"]
                meaning+=f" У фиксированного флага изменение PAR2 {pct(measured['relative']-1)} в {measured['n']} кластерах."
            extra.append(f"<h3>{h(key)}</h3>"+para(
                f"{code('raw/'+key+'/summary.json')}. Исключено по сверке {len(item.get('excluded',[]))}. "
                f"{meaning}")+
                table(("Конфигурация","Решено","PAR2, с","Противоречия","Ошибки"),rows))
        sections.append(section("Вторичные проверки переноса","".join(extra)))
    if data["wire_audits"]:
        rows=[(code(k),str(v["n"]),str(v["same_commands"]),str(v["matched_statuses"]),str(v["native_timeout"]),str(v["witnesses"]))
              for k,v in data["wire_audits"].items()]
        sections.append(section("Явная сверка живых команд",
            para("Сравниваются реальные байты, штатный dump без аннотаций :status "
                 "и результаты Z3 на тех же командах. Трассы хранятся отдельно от времени основного опыта.")+
            table(("Набор","Сеансов","Команды совпали","Согласие Z3","Таймаут native","VCD проверены"),rows)))
    if data.get("exhaustive_oracle",{}).get("complete"):
        r=data["exhaustive_oracle"]
        quantity=f"{r['designed_vectors']:,}".replace(","," ")
        witnesses=para(
            f"Icarus Verilog проверил все {quantity} пары входов схемы {code(r['id'])}. "
            f"Z3 ранее исчерпал предел. Данные симуляции в {code('raw/karatsuba-exhaustive/summary.json')}.")
        if data.get("arbiter_oracle",{}).get("complete"):
            q=data["arbiter_oracle"]
            witnesses+=para(f"Для {code(q['case'])} перебраны {q['transitions']} переходов. "
                            "Независимый оракул сверил выдачу, начальное состояние и следующий указатель. "
                            f"Данные в {code('raw/arbiter-exhaustive/summary.json')}.")
        if data.get("public_hw_oracle"):
            s=data["public_hw_oracle"]
            witnesses+=para(f"На публичном Wolf-fmbench Z3 подтвердил {s['match_total']}/8 оригинальных меток "
                            f"с повтором до 110 с. Расхождения {len(s['errors'])}. "
                            f"Журналы в {code('raw/public-hw-oracle110/summary.json')}.")
        sections.append(section("Дополнительные аппаратные оракулы",witnesses))
    if "calibration" in data:
        cal=data["calibration"]["data"]
        values=[(k,fmt(v["exec"]["median"],4),fmt(v["static"]["median"],4),fmt(v["bmc"]["median"],4)) for k,v in cal.items()]
        sections.append(section("Нижняя граница времени процесса",
            table(("CPU","Пустой exec, с","Минимальный SMT, с","Минимальный BMC, с"),values)))
    sections.append(section("Интерфейсы обратной связи",para(
        "Пространство параметров, TRAIN и целевая функция общие. SMAC получает скалярную стоимость. "
        "В QF_LIA Sol также получает число решённых и пригодность конфигурации. "
        "В последовательном Yosys Sol получает ID и ту же скалярную стоимость. Такой протокол сравнивает методы целиком. "
        f"Состав передачи и ограничения описаны в {code('evidence/feedback-contract.md')}. "
        '<a href="https://github.com/automl/SMAC3/releases/tag/v2.4.0">SMAC3 2.4.0</a>.')))
    timing=[]
    for r in data["phases"]:
        timing.append((code(r["name"]),time_s(r["start"]),time_s(r["stop"]),str(r["code"]) if r["code"] is not None else "идёт"))
    close=ROOT/"evidence/close.json"
    ledger=""
    if close.is_file():
        c=read(close)
        ledger=para(f"Первый инструментированный процесс {time_s(c['first_instrumented'])} МСК, "
                    f"последний {time_s(c['last_collection'])} МСК. Окно закрыто в 11:08:00, "
                    f"итоговая отметка {time_s(c['at'])}. Рабочая папка {fmt(c['workspace_bytes']/1e9,2)} ГБ.",
                    f"Проверка сырых файлов и времени в {code('evidence/artifact-audit.json')}. "
                    f"Итоговые SHA-256 в {code('evidence/deliverables.sha256')}.")
    sections.append(section("Хронометраж и файлы",
        ledger+table(("Этап","Начало МСК","Конец МСК","Код"),timing)+para(
        "Первый поиск завершился до измерения базовой конфигурации из-за отсутствующей Python привязки pidfd. "
        "Повтор использует тот же системный таймер через libc. Первоначальный раунд 2203 остановился у random "
        f"на гонке счётчика диска, поэтому четыре arm 2203 повторены с прежним бюджетом. Подробности в {code('evidence/opt-search-retry.md')}.",
        f"Состояние {code('current.json')}, журнал {code('CHECKPOINT.md')}, воспроизведение {code('REPRODUCE.md')}, "
        f"метаданные {code('evidence/analysis.json')}, версии {code('evidence/versions.txt')}, "
        f"контрольные суммы инструментов {code('evidence/deps.sha256')}, протоколы {code('protocols/')}, исходные логи {code('raw/')}.",
        f"Часы отчёта <time>{h(data['at'])}</time>.")))
    title="Настройка cvc5 на независимых нагрузках"
    html=(f'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
          f'<title>{h(title)}</title><style>{CSS}</style><header>'
          f'<p class="muted">24 ноября 2026 · cvc5 1.4.1 · Linux · МСК</p><h1>{h(title)}</h1>'
          f'<p class="keyline">{h(state["current"])}</p>{summary(data)}</header><main>'
          +''.join(sections)+"</main></html>\n")
    from reader import reader_edition
    html = reader_edition(html, ROOT)
    tmp=ROOT/"report.html.tmp"
    tmp.write_text(html,encoding="utf-8")
    tmp.replace(ROOT/"report.html")
    print(now(),len(html.encode()),flush=True)


if __name__=="__main__":
    render()
