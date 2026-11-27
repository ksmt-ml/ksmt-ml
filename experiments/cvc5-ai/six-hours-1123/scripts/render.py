"""Render both standalone Russian reports from structured local evidence."""
from datetime import datetime
from html import escape
import json
from pathlib import Path
import statistics

from status import now, atomic, ROOT

STYLE = """
:root{color-scheme:light;background:#f4f4ee;color:#162927;font:16px/1.55 system-ui}
body{max-width:1140px;margin:2.3rem auto;padding:0 1.25rem 3rem}header{border-top:6px solid #155b50;padding:1rem 0;margin-bottom:1.4rem}
h1,h2,h3{color:#123d39;line-height:1.18}h1{font-size:clamp(1.9rem,4.8vw,3.1rem);margin:.35rem 0}h2{margin-top:2rem}
section{background:#fffdf7;padding:1rem 1.15rem;border:1px solid #dadfd6;border-radius:.42rem;margin:1rem 0}
section h2:first-child{margin-top:.25rem}
.table{overflow-x:auto}table{border-collapse:collapse;font-variant-numeric:tabular-nums;min-width:100%;margin:.9rem 0}
th,td{padding:.38rem .58rem;border-bottom:1px solid #d5dcd2;text-align:left;vertical-align:top}th{background:#e7eee7}
code{font-size:.87em;overflow-wrap:anywhere}small,.muted{color:#435b56}a{color:#0d6254}summary{cursor:pointer}time{font-variant-numeric:tabular-nums}
nav{display:flex;flex-wrap:wrap;gap:.45rem 1.05rem;margin:1rem 0 .2rem;font-size:.9rem}nav a{text-underline-offset:.17em}
"""

SECTIONS={
    "Ответ на вопрос о настройке":("answer","Вывод"),
    "Восстановленный эксперимент":("recovery","Восстановление"),
    "Закрытый тест":("locked-test","Закрытый тест"),
    "Отбор до перезагрузки":("selection","Отбор"),
    "Сравнение стратегий на живых задачах с массивами":("array-search","Sol, SMAC и random"),
    "Экспериментальное FP кодирование в клиенте":("floating-point","FP"),
    "Расширенная панель реальных библиотек":("library-panel","Библиотечная панель"),
    "Журнал кампании":("timeline","Журнал"),
    "Методика и воспроизводимость":("methods","Методика"),
}

def read(path, default=None):
    file = ROOT/path
    return json.loads(file.read_text()) if file.is_file() else default

def f(n, places=3):
    return "—" if n is None else f"{n:.{places}f}".replace(".",",")

def pct(r):
    return f(100*r,2)+"%" if r is not None else "—"

def dt(t):
    return (datetime.fromisoformat(t).strftime("%d.%m.%Y %H:%M:%S") + " МСК") if t else "—"

def tbl(headers, rows):
    return '<div class="table"><table><thead><tr>'+''.join(f"<th>{escape(str(x))}</th>" for x in headers)+(
           "</tr></thead><tbody>")+''.join("<tr>"+''.join(f"<td>{escape(str(x))}</td>" for x in r)+"</tr>" for r in rows)+(
           "</tbody></table></div>")

def paragraph(value):
    return f"<p>{escape(value)}</p>"

def section(title, text):
    key=SECTIONS.get(title)
    anchor=f' id="{key[0]}"' if key else ""
    return f"<section{anchor}><h2>{escape(title)}</h2>{text}</section>"

def doc(title, subtitle, body):
    links=[f'<a href="#{anchor}">{escape(label)}</a>' for anchor,label in SECTIONS.values()
           if f'<section id="{anchor}">' in body]
    nav='<nav aria-label="Разделы отчёта">'+"".join(links)+"</nav>" if links else ""
    return (f'<!doctype html>\n<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{escape(title)}</title><style>{STYLE}</style></head><body><header><h1>{escape(title)}</h1>'
            f'<p>{escape(subtitle)}</p>{nav}</header><main>{body}</main><footer><small>Обновлено {dt(now())}, butvinm-work.</small></footer></body></html>\n')

GATES = {"paths":"сумма путей","edges":"рёбра","steps":"шаги","coverage_pp":"покрытие",
         "step_errors":"ошибки шага","failed":"штрафные методы","solver_unknown":"UNKNOWN",
         "solver_timeouts":"таймауты решателя"}

def short_option(x):
    return ", ".join(k+"="+v for k,v in x.items()) or "default"

def selection():
    diag=read("recovery/evidence/diagnosis.json")
    if not diag:
        return paragraph("Проверяется архив поиска.")
    baseline=[]
    trials=[]
    for engine in ("smac","sol"):
        r=diag[engine]
        for mode in ("train","validation"):
            x=r[mode]
            baseline.append([engine.upper(),mode,x["n"],f(x["par2_ms"]/x["n"]/1000),f(x["wall_ms"]/1000,1),
                             x["paths"],f(x["coverage"]/x["n"],2),x["steps"],x["step_errors"],
                             x["solver_unknown"],x["exploration_timeouts"],x["failed"]])
        for t in r["trials"]:
            x=t["result"]
            trials.append([engine.upper(),t["mask"],short_option(t["options"]),pct(x["par2_ratio"]),pct(x["wall_ratio"]),
                          pct(x["paths_ratio"]),pct(x["edges_ratio"]),pct(x["steps_ratio"]),f(x["coverage_delta_pp"],2),
                          x["totals"]["step_errors"],x["totals"]["solver_unknown"],
                          ", ".join(GATES.get(k,k.replace("paths:","пути ")) for k in x["violations"])])
    times=tbl(("Стратегия","Начало","Последний train","Выбор","Время, с","Новых / допущено"),
          [[a.upper(),dt(diag[a]["choice"]["start_msk"]),dt(diag[a]["choice"]["search_end_msk"]),
            dt(diag[a]["choice"]["end_msk"]),f(diag[a]["choice"]["wall_s"],1),
            f'{diag[a]["choice"]["new_trials"]} / 0'] for a in ("smac","sol")])
    smac_masks={t["mask"] for t in diag["smac"]["trials"]}
    sol_masks={t["mask"] for t in diag["sol"]["trials"]}
    intro=paragraph(f"На контрактной проверке прошли все {len(diag['eligible'])} комбинации. SMAC оценил 7 новых вариантов, Sol — 6. Измерено {len(smac_masks | sol_masks)} уникальных недефолтных конфигураций, включая {len(smac_masks & sol_masks)} измеренные обеими стратегиями. Каждый пакет прошёл проверку полноты. Ни один вариант не прошёл все ограничения train. На validation каждая стратегия измерила только свой default. Оба выбора зафиксированы 22.11.2026 в 17:39:20 МСК до закрытого теста.")
    interpretation=paragraph("В опыте Sol все шесть вариантов потеряли больше 1 п.п. покрытия и опустили пути commons-pool2 ниже проектного порога 95%. Часть вариантов формально экономила больше 2% PAR2. Лучшие по времени маски 20 и 4 оставили 97,22% и 97,23% PAR2 default соответственно. У SMAC ближайшая по времени маска 12 дала 97,92% PAR2 и 101,87% полного времени JVM при меньшем числе путей и потере покрытия. Маска 17 сохранила пороги работы и увеличила число штрафных методов с 28 до 29.")
    precision=paragraph("Два последовательных train-замера default различались на 2,75% PAR2 и на 1,44 п.п. среднего покрытия. В одном было 58 исключений шага, в другом 63. Такой межоконный размах сопоставим с порогами выбора. Он включает изменение исполненной работы и времени, поэтому проверяем A/A отдельно в парных блоках.")
    calls=diag["sol"]["calls"]
    inference=paragraph(f"Sol провёл {len(calls)} отдельных вызовов. В первом вызове инструмент чтения не открыл train, модель явно сообщила об этом и предложила одиночный bv-eager-eval на основании знания решателя. Следующие пять запросов содержали фактический снимок train на stdin. Модель выбирала сначала одиночные переключатели, затем bv-eager-eval=true вместе со static-learning=false, объясняя выбор через наблюдённые потери путей и ошибки. Все предложения были новыми и допустимыми по пространству. Задержка шести запросов суммарно {f(sum(x['status']['wall_ms'] for x in calls)/1000,1)} с. SMAC использовал нативный ask/tell, включая исходный default. Поиск упёрся в стоимость полного train-пакета и ограничения качества.")
    penalties=read("recovery/evidence/penalty-decomposition.json")
    decomposition=""
    if penalties:
        by_gate=[[a.upper(),*[penalties[a][k] for k in ("only_time","only_work","only_reliability")]] for a in ("smac","sol")]
        data=[]
        for a,r in penalties.items():
            for t in r["rows"]:
                data.append([a.upper(),t["mask"],f(t["delta_ms"]/1000,2),f(t["penalty_delta_ms"]/1000,2),
                 f(t["delta_successful_ms"]/1000,2),f(t["observed_all_ms"]/1000,2)])
        decomposition=(paragraph("Во всех шести пакетах Sol стало на один штрафной метод меньше. Это даёт −16 с к суммарному PAR2. Сумма времён успешных методов при каждом варианте выросла на 1,26–6,12 с. Наборы успешных методов менялись, поэтому декомпозиция описывает механизм отчётного выигрыша.")+tbl(
          ("Стратегия","Прошёл бы только время","Прошёл бы только работу","Прошёл бы только надёжность"),by_gate)+
          "<details><summary>Разложение суммарного PAR2 train, секунды</summary>"+tbl(
          ("Стратегия","Маска","Δ PAR2","Δ штрафа","Δ времени успешных","Δ сырого времени всех методов"),data)+"</details>")
    return (intro + times + interpretation + precision + inference + decomposition +
       "<h3>Контрольные измерения default</h3>"+
       tbl(("Стратегия","Раздел","Методы × повтор","PAR2/метод, с","Сумма JVM, с","Пути","Покрытие, %","Шаги","Ошибки шага","UNKNOWN","Стоп по времени","Штрафные методы"),baseline)+
       "<details><summary>Все проверенные варианты и нарушенные пороги</summary>"+
       tbl(("Плечо","Маска","Опции","PAR2 / A","JVM / A","Пути / A","Рёбра / A","Шаги / A","Δ покрытия, п.п.","Ошибки шага","UNKNOWN","Нарушения"),trials)+"</details>")

def calibration():
    c=read("recovery/evidence/post-boot-calibration.json")
    if not c:
        return ""
    rows=[]
    for core in ("12","13","14","15"):
        s=[t["result"]["wall_ms"] for t in c["standalone"][core]]
        t=[x[core]["result"]["wall_ms"] for x in c["concurrent"]]
        rows.append([core,c["before"][f"siblings_{core}"],f(statistics.median(s),1),
                     f(statistics.median(t),1),f(c["live_smoke"][core]["wall_ms"]/1000,2)])
    return paragraph(f"Калибровка {dt(c['before']['at'])} — {dt(c['after']['at'])}. Loadavg до — {c['before']['loadavg']}, после — {c['after']['loadavg']}. SHA-256 на фиксированном буфере измерен отдельно (3 повтора) и одновременно на четырёх ядрах (6 повторов). На каждом ядре отдельно проверен живой cvc5.")+tbl(("CPU","SMT-соседи","SHA-256 отдельно, мс","SHA-256 вчетвером, мс","Живой smoke JVM, с"),rows)

def result_ci(row):
    return f(row["value"])+" ["+f(row["ci"][0])+"; "+f(row["ci"][1])+"]"

def recovery_results():
    x=read("recovery/evidence/summary.json")
    if not x:
        return paragraph("8 полных архивных блоков сохранены. Запущен повтор 8 неполных или отсутствующих блоков в исходном порядке и с исходными параметрами. В карантине 3 завершённых JVM из неполных блоков и артефакты оборванных процессов.")
    rows=[]
    counts=[]
    for epoch,name in (("pre-restart","До перезагрузки"),("post-restart","После перезагрузки"),("combined","Вместе")):
        if epoch not in x:
            continue
        b=x[epoch]
        for label, contrast in (("Z3 / cvc5 default (среднее 3 JVM)",b["z3_vs_pooled"]),
                                ("SMAC-метка / default (A/A)",b["contrasts"]["smac"]),
                                ("Sol-метка / default (A/A)",b["contrasts"]["sol"])):
            rows.append([name,b["blocks"],len(b["projects"]),label,*[result_ci(contrast[k]) for k in
                                                       ("par2_ms","wall_ms","paths","coverage","steps","check_ms")]])
        for arm, z in b["arm_totals"].items():
            counts.append([name,arm,z["n"],z["jvms"],f(z["par2_ms"]/z["n"]/1000),f(z["wall_ms"]/1000,1),
            z["paths"],z["edges"],z["steps"],f(z["coverage"]/z["n"],2),z["checks"],z["exploration_timeouts"],
            z["solver_timeouts"],z["other_unknown"],z["step_errors"],z["failed"],z["jvm_failed"]])
    chronology=[(name,dt(x[k]["start"]),dt(x[k]["end"]),", ".join(f"{p}: {n}" for p,n in x[k]["project_blocks"].items()))
                for k,name in (("pre-restart","До перезагрузки"),("post-restart","После перезагрузки")) if k in x]
    a=x["acquisition"]
    merged=x.get("combined",{}).get("z3_vs_pooled")
    insight=paragraph(f"Для всех блоков Z3 дал PAR2 {result_ci(merged['par2_ms'])}, полное время JVM {result_ci(merged['wall_ms'])} и шаги {result_ci(merged['steps'])} от cvc5. Это результат всего живого клиента с другим решателем, числом шагов и отказами. Корректность каждого решения SMT в библиотечном тесте отдельно не устанавливали.") if merged else ""
    epoch=read("recovery/evidence/epoch-variance.json")
    if epoch:
        e=epoch["metrics"]["par2_ms"]
        insight+=paragraph(f"Для одинаковых метода и seed в обеих стратах нашлись {epoch['common_keys']} ключа всего в двух проектах. "
           f"Отношение PAR2 того же cvc5 default после/до — {result_ci(e)}. Интервал описательный для двух кластеров. "
           "Между измерениями изменились эпоха и порядок исполнения. Архивного профиля нагрузки до перезагрузки нет.")
    return (paragraph(f"Приняты {a['counts'].get('pre-restart',0)} архивных и {a['counts'].get('post-restart',0)} повторённых блоков из {a['scheduled']}. Сравнения выполняются внутри одного блока и одной эпохи. При одинаковых опциях три cvc5 JVM усредняются внутри блока для сравнения с Z3, сохраняя исходные сравнения с отдельным default как проверку устойчивости. Строки Sol и SMAC ниже измеряют разброс одинаковой конфигурации.")
        +tbl(("Страта","Начало","Конец","Блоки по проектам"),chronology)+insight
        +paragraph("95% интервалы получены бутстрэпом целых проектов, 10 000 выборок. Повторы уменьшают шум внутри проекта. Для покрытия показана абсолютная разность в п.п. При четырёх проектах интервалы отражают изученную выборку.")
        +tbl(("Страта","Блоки","Проекты","Контраст","PAR2","JVM wall","Пути","Δ покрытия, п.п.","Шаги","Check time"),rows)
        +"<details><summary>Абсолютные значения и отказы</summary>"
        +tbl(("Страта","JVM-метка","Строки","JVM","PAR2/метод, с","JVM wall, с","Пути","Рёбра","Шаги","Покрытие, %","Checks","Стоп wall","Таймаут решателя","Прочий UNKNOWN","Ошибка шага","Штрафные методы","Сбой JVM"),counts)
        +"</details>")

def progress():
    logfile=ROOT/"evidence/timeline.jsonl"
    log=[json.loads(l) for l in logfile.read_text().splitlines()] if logfile.is_file() else []
    return tbl(("Время","Этап"),[(dt(r["at"]),r["completed"] or r["message"]) for r in log])

def followups():
    text=paragraph("Зафиксированы 20 блоков по пяти библиотекам, 30 новых внешних классов. Повтор default, дозированный контроль 250 мкс на check, режимы 6 с и фиксированных шагов и проверка общего префикса по SHA-256. На каждом проекте два seed, с двумя JVM-повторами каждый (один с трассой).")
    result=read("followup/evidence/power-summary.json")
    if result:
        text+=paragraph(f"{dt(result['start'])} — {dt(result['end'])}. Принято {result['available']} из {result['planned']} блоков, {result['jvms']} JVM, сбоев JVM {result['jvm_failures']}.")
        if result["available"]==result["planned"]:
            text+=paragraph("Фиксирование шагов улучшило совпадение A/A по счётчикам с 36 до 45 из 60 без трассы, а совпадение полных трасс выросло с 18 до 28 из 60. Моделируемая мощность увидеть выигрыш 2% по PAR2 была 0,12% при 6 с и 8,32% с фиксированными шагами. Доза в 250 мкс в фиксированном режиме оказалась меньше разброса пар: её повышение времени check не воспроизвелось устойчиво.")
        labels={"wall":"6 с / 4000","fixed":"Фиксированные шаги"}
        rows=[]
        for tag, cell in result["comparisons"].items():
            mode, arm, trace=tag.split("-")
            ratio=cell["ratio"]
            rows.append([labels[mode],"A/A" if arm=="B" else "Доза / A","Да" if trace=="trace" else "Нет",
              cell["n"],cell["aggregate_equivalent"],cell["identical_trace"] if trace=="trace" else "—",
              f'{cell["checks_on_common_prefix"]}/{cell["compared_checks"]}' if trace=="trace" else "—",
              result_ci(ratio["par2_ms"]),result_ci(ratio["analyze_ms"]),result_ci(ratio["check_ms"]),
              result_ci(cell["dose_interval"]) if cell.get("dose_interval",{}).get("value") is not None else f(cell["observed_delay_recovery"])])
        text+=tbl(("Режим","Контраст","Трасса","Пар методов","Совпало по метрикам","Совпала полная трасса",
                  "Общий префикс checks","PAR2","Анализ","Check","Возврат дозы"),rows)
        detail=[]
        for key,c in result["comparisons"].items():
            if c["sensitivity"]:
                for measure,d in c["sensitivity"].items():
                    if d:
                        detail.append([labels[key.split("-")[0]],measure,d["n"],*[pct(d["power"][v]) for v in ("0.02","0.05","0.1")],
                                       pct(d["minimum_at_80pct"])])
        if detail:
            text+=paragraph("Разрешение моделировали из центрированных логарифмов A/B по проектам. В каждой симуляции добавляли фиксированный эффект, затем применяли односторонний знаковый перестановочный критерий (α=0,05). Порог 80% указан по заданной сетке и зависит от этих проектов. Трассированные времена показаны отдельно.")+tbl(("Режим","Метрика","Проектов","Мощность 2%","Мощность 5%","Мощность 10%","Порог 80%"),detail)
        cross=result.get("cross_budget",{})
        if cross:
            rows=[]
            for typ,cell in cross.items():
                r=cell["ratio"]
                ta,tb=(cell["stops"][n].get("method_timeout",0) for n in ("fixed","wall"))
                rows.append([typ,cell["n"],cell["aggregate_equivalent"],result_ci(r["paths"]),result_ci(r["steps"]),
                    result_ci(r["analyze_ms"]),f"{ta}/{tb}"])
            text+=tbl(("F / W","Пар","Совпала работа","Пути","Шаги","Анализ","Стоп по времени F/W"),rows)
        bad=[]
        for key,cell in result["comparisons"].items():
            if key.split("-")[1]!="P":
                continue
            for arm,c in cell["totals"].items():
                bad.append([key,arm,c["checks"],c["failed"],c["step_errors"],c["solver_timeouts"],
                            c["other_unknown"],c["missing"],cell["stops"][arm].get("method_timeout",0)])
        text+="<details><summary>Надёжность по режимам и дозе</summary>"+tbl(
            ("Контраст","Плечо","Checks","Штрафные методы","Исключения шага","Таймаут решателя","Прочий UNKNOWN","Пропущено","Стоп wall"),bad)+"</details>"
    else:
        text+=paragraph("Парные JVM выполняются. Метрики появятся после сверки завершённых блоков.")
    return section("Контроль чувствительности",text)

def error_origins():
    raw=read("diagnostics/evidence/historical-errors.json")
    if not raw:
        return ""
    names={"usvm_stepscope":"USVM StepScope","usvm_null_field":"USVM чтение поля nullRef",
           "usvm_array_reflection_cast":"USVM моделирование массивов / cast","jacodb_missing_class":"JacoDB отсутствующая зависимость",
           "ksmt_cvc5_fp_ieee":"KSMT cvc5 fp.to_ieee_bv","other":"Прочее"}
    categories=sorted({k for x in raw.values() for k in x["counts"]})
    return section("Классы ошибок клиента",
        paragraph("По предупреждениям и стекам выделены разные этапы. Архивный train и validation объединены по причине, тест разделён по перезагрузке. Единица счёта — исключение шага.")+
        tbl(("Причина","Train и validation","Тест до перезагрузки","Тест после перезагрузки"),
           [[names[k],*(raw[e]["counts"].get(k,0) for e in ("selection","pre-restart","post-restart"))] for k in categories]))

def fp_adapter():
    x=read("diagnostics/evidence/fp.json")
    if not x:
        return ""
    opts=read("diagnostics/inputs/fp-schedule.json")["option_sets"]
    rows=[]
    for arm, modes in x["summary"].items():
        for mode,c in modes.items():
            rows.append([arm,short_option(opts[arm]) if arm!="z3" else "Z3",mode,*[
                         c.get(k,0) for k in ("SAT","UNSAT","unsupported","UNKNOWN","error","incorrect","jvm_failure")]])
    return section("Контракт FP в адаптере KSMT",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Проверены {len(x['runs'])} JVM. Два размера FP, 14 IEEE значений, варианты SAT и UNSAT. У SAT проверено значение модели.")+
        paragraph("Прямое представление просит адаптер перевести fp.to_ieee_bv. Обратное связывает свежий вектор с исходным FP через равенство после восстановления IEEE числа. Для Java-канонизированного NaN используется фиксированный шаблон. Проверяются также различимые знаки нулей.")+
        tbl(("Решатель","Опции","Представление","SAT","UNSAT","Нет поддержки","UNKNOWN","Ошибки","Неверный ответ/модель","Сбой JVM"),rows))

def native_theories():
    x=read("native/evidence/summary.json")
    if not x:
        return ""
    rows=[]
    for a, details in x["results"].items():
        for d in details:
            totals=d["totals"]
            rows.append([d["family"] or "Все",a,short_option(x["option_sets"][a]),d["programs"],d["blocks"],
                         result_ci(d["ratios"]["par2_ms"]),result_ci(d["ratios"]["cpu_known_ms"]),
                         "/".join(map(str,totals["unknown"])),"/".join(map(str,totals["incorrect"])),
                         "/".join(map(str,totals["jvm_error"]))])
    return section("Нативный cvc5 на одинаковых SMT-потоках",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Z3 согласился с алгебраическим оракулом у {x['oracle_passed']} из {x['oracle_total']} программ. Выполнено {x['blocks']} парных блоков из {x['planned']}, {x['jvms']} JVM. Лимит cvc5 1,6 с на check, PAR2 штраф 3,2 с за неправильный, неизвестный или пропущенный ответ.")+
        paragraph("Нативные часы CPU относятся к потоку check и парам с известным одинаковым правильным исходом. Доверительные интервалы пересчитываются по целым программам внутри семейства и по семействам в общей оценке.")+
        tbl(("Семейство","Плечо","Опции","Программ","Блоков","PAR2 / A","Thread CPU / A","UNKNOWN / A","Неверные / A","Сбой JVM / A"),rows))

def warm_screen():
    x=read("screening/evidence/warm-summary.json")
    if not x:
        return ""
    compact=read("screening/evidence/warm-compact-summary.json")
    first=paragraph(f"{dt(x['start'])} — {dt(x['end'])}. В исходной схеме вместе выполнялись 45–54 анализа. "
          "Все 12 JVM прерваны до окончания. Восемь достигли 2,5 млн КиБ RSS, четыре — 135 с. Сырые завершённые методы сохранены. "
          "В компактной схеме в JVM по одному или два метода, GC между анализами и меньше Xmx.")
    if not compact:
        return section("Пакетирование живого USVM",first+paragraph("Компактные пары выполняются, допуск вариантов ещё не оценён."))
    ops=read("screening/inputs/warm.json")["arms"]
    contents=[]
    for k,r in compact["table"].items():
        key,trace=k.rsplit("-",1)
        ratio=r["ratios"]
        contents.append([key,ops[key][0],short_option(ops[key][1]),trace,r["n"],r["equivalent"],r["exact"] if trace=="trace" else "—",
                         result_ci(ratio["paths"]),result_ci(ratio["par2_ms"]),r["stable"],
                         result_ci(r["stable_ratios"]["check_ms"]),pct(r["stable_smt_share"]),
                         "/".join(map(str,r["sum"]["failed"]))])
    theory=[]
    for p,values in compact["theories"].items():
        for combination,count in sorted(values.items(),key=lambda item:-item[1]):
            theory.append((p,combination,count))
    return section("Пакетирование живого USVM", first+
        paragraph(f"{dt(compact['start'])} — {dt(compact['end'])}. Компактная схема. Проекты {', '.join(compact['projects'])}. JVM {compact['jvms']}, полных {compact['complete']}. "
                  "Каждый анализ создаёт собственную USVM и cvc5. Предел 1500 шагов, seed 0 и 71, порядок вариантов внутри метода запечатан.")+
        tbl(("Плечо","Селектор","Опции","Трасса","Пар","Метрики равны","Трасса равна","Пути","PAR2","Чистые пары с check","Check на чистых парах","Доля SMT","Штрафные методы / A"),contents)+
        tbl(("Проект","Теории во входе KSMT default","Checks в трассированных парах"),theory))

def time_shares():
    x=read("diagnostics/evidence/time-budget.json")
    if not x:
        return ""
    rows=[]
    for label,r in x["groups"].items():
        a=r["all"]
        rows.append([label,a["jvms"],a["n"],pct(a["check_share_wall_ms"]),pct(a["check_share_jvm_ms"]),
                     pct(a["max_gain_10pct_of_check_wall"]),
                     pct(.02/a["check_share_jvm_ms"]) if a["check_share_jvm_ms"] else "—"])
    return section("Как check влияет на время всего клиента",
        paragraph("Показаны заводские JVM из исходного train, validation и нового A/A. Расчёт по доле check предполагает неизменную работу. Таймер участка включает работу интерфейса.")+
        tbl(("Набор","JVM","Методов × повтор","Check / время методов","Check / время JVM","Экономия метода при −10% check",
            "Сокращение check для −2% полного JVM"),rows))

def live_gate():
    g=read("search/evidence/live-gate.json")
    if not g:
        return ""
    rows=[]
    for name,r in g["catalog"].items():
        rows.append([name,short_option(r["value"]),r["shared"],r["n"],r["projects"],r["projects_won"],
                     f(r["check_ratio"]),f(r["analysis_ratio"]),"Да" if r["pass_gate"] else "Нет"])
    return section("Допуск повторного живого поиска",
        paragraph(f"Решение {dt(g['at'])}. Чистые A/B пары образуют общую выборку. Для кандидата требуется та же трасса, ≥5% экономия в check на двух проектах и ≤2% регрессия времени анализа.")+
        paragraph("Допуск достигнут." if g["gate"] else "Допуск не достигнут. Повторный живой поиск на этом наборе не запускался. Тестовые проекты оставались отделены во время этой проверки.")+
        tbl(("Вариант","Опция","Совпало","Пар","Проектов","Проектов с выигрышем","Check / A/B","Анализ / A/B","Допуск"),rows))

def oracle_live():
    x=read("oracle-live/evidence/summary.json")
    if not x:
        return ""
    rows=[]
    for name,r in sorted(x["summary"].items()):
        p,a,kind=name.split("|")
        rows.append([p,a,short_option(x["arms"][a]) if a!="z3" else "Z3",kind,r["jvms"],r["valid_all"],
                     str(r["categories"])+"/"+str(7*r["jvms"]),r["checks"],r["step_errors"],
                     r["solver_timeouts"],r["other_unknown"],r["wrong"],r["witness_errors"],r["jvm_failed"]])
    return section("Живой контроль представлений IEEE",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Два класса Java, восемь блоков и {x['jvms']} JVM. "
        "Независимый Java оракул задавал семь достижимых категорий для каждого представления. Каждый полученный USVM свидетель исполнен на тех же аргументах реальным Java-методом.")+
        paragraph("Маршрут raw вызывает преобразование FP в биты. Все 24 cvc5 прогона raw потеряли доступные категории через исключение шага. В Z3 все восемь raw прогонов покрыли семь категорий. Общий вход в виде BV дал полный результат для каждого плеча, включая cvc5. На прямых FP предикатах в 64 битах часть cvc5 прогонов достигла таймаута check. Ни одного неверного проверенного конкретного свидетельства не обнаружено.")+
        tbl(("Класс","Плечо","Опции","Представление","JVM","Полных","Категории","Checks","Ошибки шага",
            "Таймаут","Прочий UNKNOWN","Неверные","Ошибки свидетеля","Сбой JVM"),rows))

def bv_backend():
    x=read("bv-study/evidence/summary.json")
    gate=read("bv-study/evidence/contract.json")
    if not gate:
        return ""
    intro=paragraph("После первого check с текущим инкрементальным режимом bv-propagate у default меняется с true на false. "
                 "Запрошенное bv-propagate=false совпадает со снимком default по прочитанным полям. "
                 "Запрошенное bv-assert-input=true сбрасывается, поэтому это плечо исключено контрактом.")
    deliveries=[]
    for arm,r in gate.items():
        options=x["option_sets"][arm] if x else read("bv-study/inputs/manifest.json")["option_sets"][arm]
        deliveries.append([arm,short_option(options),"Да" if r["passed"] else "Нет",
          "Да" if r["observed_default_alias"] else "Нет",
          ", ".join(f"{k}={v[1]}" for k,v in r["changes_from_default"].items())])
    intro+=tbl(("Вариант","Запрос","Контракт","Совпала проекция default","Эффективные отличия"),deliveries)
    decision=read("native-search/evidence/gate.json")
    if decision:
        intro+=paragraph(f"{dt(decision['at'])}. Семейный допуск прошли {decision['heads']} вариантов. "
                         "Отдельная серия 09b проверяет перенос двух перспективных опций и их комбинации.")
    if not x:
        return section("Доставка нативных BV опций",intro+paragraph("Парные потоки выполняются после контракта."))
    rows=[]
    for label,ds in x["results"].items():
        for v in ds:
            t=v["totals"]
            rows.append([label,v["family"] or "Все",v["programs"],v["blocks"],
               result_ci(v["ratios"]["par2_ms"]),result_ci(v["ratios"]["cpu_known_ms"]),
               "/".join(map(str,t["unknown"])),"/".join(map(str,t["solver_timeouts"])),
               "/".join(map(str,t["incorrect"])),"/".join(map(str,t["jvm_error"]))])
    return section("Доставка нативных BV опций",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Подтверждено Z3 {x['oracle_passed']} программ, {x['blocks']} парных блоков, {x['jvms']} JVM.")+
        intro+"<details><summary>Результаты по четырём семействам</summary>"+
        tbl(("Вариант","Семейство","Программ","Блоков","PAR2 / A","CPU / A","UNKNOWN / A","Таймаут / A","Неверные / A","Сбой JVM / A"),rows)+"</details>")

def repeat_precision():
    x=read("diagnostics/evidence/repeat-precision.json")
    if not x:
        return ""
    rows=[]
    for mode,v in x["result"].items():
        for metric,r in v.items():
            if not r:
                continue
            t=r["illustrative_projects"]["2"]
            rows.append([mode,metric,r["projects"],r["repeats"],f(r["log_stdev_within"]),f(r["log_stdev_between"]),
               t["2"],t["5"],t["10"]])
    return section("Повторы и независимые проекты",
        paragraph("Оценка планирования из вариации логарифмов A/A при двух неинструментированных повторах на проект. "
        "Последние колонки показывают расчёт числа проектов при нормальной аппроксимации для односторонней мощности 80%. "
        "Пять наблюдаемых проектов дают грубую оценку компонент дисперсии.")+
        tbl(("Режим","Метрика","Проектов","JVM повторов","Шум повторов","Межпроектный компонент","2%","5%","10%"),rows))

def stable_screen():
    x=read("stable/evidence/summary.json")
    if not x:
        return ""
    results=[]
    for name,r in x["results"].items():
        mm=r["metrics"]
        results.append([name,short_option(x["arms"][name]),r["n"],r["aa_same"],r["identical"],
                        result_ci(r["matched"]["check_ms"]),result_ci(r["matched"]["analyze_ms"]),
                        result_ci(r["ratios"]["pen"]),"/".join(map(str,mm["unknown"])),
                        "/".join(map(str,mm["step_errors"])),"Да" if r["accept"] else "Нет"])
    p=x["results"]["P"]["matched"].get("dose")
    dose=paragraph("Для точно совпавшей работы оценка измеренного приращения check к дозе — "+result_ci(p)+".") if p else ""
    robustness=read("stable/evidence/dose-sensitivity.json")
    if robustness:
        dose+=paragraph("Направление совпало с дозой в четырёх из пяти проектов. Без lz4-java отношение приращения к дозе "
             +f(robustness["omitting_project"]["lz4-java"])+". Нагрузка lz4 меняет знак общей оценки.")
    return section("Холодный скрининг на устойчивых методах",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. {len(x['projects'])} библиотек. Блоков {x['blocks']}, JVM {x['jvms']}. "
        f"Сбоев JVM {x['errors']}. Фиксировали 4000 шагов и метки A и B для default и P для default с дозой. Доза P — 2 мс на check.")+
        paragraph("Отбор методов основан на точном A/A независимой серии 01. Внутренняя общая выборка образована свежими A/B. Корректность сравнения при испытании кандидата дополнительно требует полной совпавшей наблюдаемой траектории.")+dose+
        tbl(("Плечо","Опции","Пар","Точное A/B","Точное плечо","Check / A/B","Анализ / A/B","PAR2 / A",
            "UNKNOWN / A","Ошибки шага / A","Допуск"),results))

def native_budget():
    x=read("budget/evidence/summary.json")
    if not x:
        return ""
    rows=[]
    for k,r in x["scores"].items():
        rep=x["repeatability"].get(k,{})
        rows.append([k,r["n"],r["correct"],r["timeout"],r["resourceout"],r["incorrect"],r["missing"],r["jvm_failed"],
                     f(r["cpu_ns"]/1e9),f(r["wall_ns"]/1e9),rep.get("flipped_queries",0),rep.get("repeated_queries",0)])
    cal=[[k,r["n"],r["correct"],r["timeout"],r["resourceout"],r["incorrect"],f(r["wall_ns"]/1e9)]
            for k,r in x["calibration"].items()]
    clock=[read(p.relative_to(ROOT)) for p in (ROOT/"budget/raw/calibration").glob("*/*/status.json")]
    cal_clock=(paragraph(f"Калибровочные JVM {dt(min(r['start_msk'] for r in clock))} — {dt(max(r['end_msk'] for r in clock))}.")
               if clock else "")
    return section("Лимит времени и лимит ресурса cvc5",
        cal_clock+paragraph(f"Тест {dt(x['start'])} — {dt(x['end'])}. По калибровке выбран {x['chosen']} для сопоставления с лимитом 120 мс. "
           f"В тестовой серии {x['observations']} пар с тем же математическим оракулом. В R действует потолок 800 мс.")+
        tbl(("Условие","Запросов","Верные","Timeout","Resourceout","Неверные","Пропуски","Сбои JVM","CPU, с","Wall, с",
             "Исход менялся","Повторённых ключей"),rows)+
        "<details><summary>Калибровочная сетка</summary>"+tbl(("Лимит","Запросов","Верные","Timeout","Resourceout","Неверные","Wall, с"),cal)+"</details>")

def pure_resource():
    x=read("pure-resource/evidence/summary.json")
    if not x:
        return ""
    rows=[[a,short_option(x["options"][a]),*[v.get(k,0) for k in ("n","correct","timeout","resourceout","other_unknown","incorrect","missing","jvm_failed")]]
          for a,v in x["cells"].items()]
    timings=[[a,"A" if a=="B" else "B",result_ci(v["cpu"]),result_ci(v["wall"])] for a,v in x["timing"].items()]
    return section("Ресурс cvc5 с дополнительным ограничением времени",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. {x['programs']} программ, {x['blocks']} из {x['planned']} блоков, {x['jvms']} JVM. "
            "Диагностический набор отобран по прошлым причинам остановки. B убирает внутренний tlimit при том же ресурсе. "
            "C повышает ресурс. Общий JVM watchdog во всех трёх плечах восемь секунд.")+
        tbl(("Плечо","Опции","Запросов","Верные","Таймаут","Resourceout","Прочий UNKNOWN","Неверные","Пропуски","Сбои JVM"),rows)+
        tbl(("Плечо","База","CPU на совместно верных","Wall на совместно верных"),timings)+
        "<details><summary>Смена статуса или причины в B относительно A</summary>"+
        tbl(("A и B","Пар"),list(x["changes"].items()))+"</details>")

def logic_study(folder="logic-study"):
    x=read(folder+"/evidence/summary.json")
    if not x:
        return ""
    rows=[[a,p,*[g.get(k,0) for k in ("n","correct","unknown","timeout","incorrect","missing","jvm_failed")]]
          for a,projects in x["outcomes"].items() for p,g in projects.items()]
    times=[[a,r["n"],result_ci(r["cpu"]),result_ci(r["wall"])] for a,r in x["timing"].items()]
    title,context={
        "logic-study":("Объявление SMT логики","Первые две проверенные программы каждого семейства. "),
        "logic-complement":("Перенос эффекта объявления логики","Другие программы тех же четырёх семейств, без повторов из первого опыта. "),
        "logic-unused-uf":("Объявление неиспользуемых UF","U меняет только объявление на QF_UFBV или QF_AUFBV. Программы из первого опыта. "),
    }[folder]
    sensitivity=read("logic-unused-uf/evidence/pair-sensitivity.json") if folder=="logic-unused-uf" else None
    extra=""
    if sensitivity:
        contrast=sensitivity["contrasts"]
        extra=(paragraph("Интервал U/B содержит единицу. При одинаковом весе семейств U/B близко к единице, тогда как ALL остаётся медленнее U. "
                        "Одного объявления UF недостаточно, чтобы объяснить наблюдаемую издержку ALL. Семейств четыре.")+
              tbl(("Пара","Совместно правильных в четырёх плечах","CPU по суммарному времени","CPU с равным весом семейств"),
                  [[k,r["joint"],result_ci(r["cpu"]),f(r["equal_family_cpu"])] for k,r in contrast.items()]))
    return section(title,
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. {x['programs']} программ, {x['blocks']} блоков из {x['planned']}, "
             f"{x['jvms']} JVM. A/B используют исходную QF_BV или QF_ABV. C меняет ровно первую строку на ALL. "
             f"{context}Общие опции {short_option(x['options'])}. Верные ответы подтверждены двумя критериями.")+
        tbl(("Плечо","Семейство","Запросов","Верные","UNKNOWN","Таймауты","Неверные","Пропуски","Сбои JVM"),rows)+
        tbl(("Плечо / A","Совместно верных","CPU","Wall"),times)+extra)

def trace_cause():
    x=read("trace-cause/evidence/summary.json")
    if not x:
        return ""
    rows=[]
    for arm,r in x["comparisons"].items():
        for flags,n in r["counts"].items():
            path,query,ops=["Да" if v=="True" else "Нет" for v in flags.split()]
            rows.append([arm,r["paired"],path,query,ops,n,f'{r["total_path_prefix"]}/{r["common_steps"]}',
                         f'{r["total_query_prefix"]}/{r["common_checks"]}'])
    return section("Откуда расходятся траектории",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. {len(x['projects'])} проектов, {x['blocks']} блоков, {x['jvms']} JVM. "
                 "SHA-256 вычислялся отдельно для порядка выбора состояний USVM, входных утверждений и допущений KSMT и прочих операций solver. "
                 "Одинаковая последовательность мест в программе не гарантирует одинакового решения SMT. Хеш входа чувствителен к именам и порядку термов.")+
        tbl(("Сравнение с A","Пар","Пути","Запросы","Операции","Пар данной комбинации","Общий префикс шагов",
             "Общий префикс checks"),rows))

def order_results():
    x=read("order-trace/evidence/summary.json")
    if not x:
        return ""
    rows=[]
    for arm,comparison in x["comparisons"].items():
        c=comparison["totals"]
        rows.append([arm,*[c.get(k,0) for k in ("pairs","no_pair","same_path","same_order","same_multiset","same_full")],
                     f'{c.get("reorder_explains",0)}/{c.get("same_path_order_drift",0)}',
                     f'{c.get("ordered_prefix",0)}/{c.get("compared_prefix",0)}',
                     f'{c.get("multiset_prefix",0)}/{c.get("compared_prefix",0)}',
                     result_ci(comparison["prefixes"]) if comparison["prefixes"] else "—"])
    coverage=[]
    for a,c in x["coverage"].items():
        coverage.append([a,pct(c["paths_ratio"]),pct(c["edges_ratio"]),pct(c["steps_ratio"]),f(c["coverage_delta_pp"]),
                pct(c["par2_ratio"]),pct(c["wall_ratio"]),", ".join(GATES.get(k,k) for k in c["violations"]) or "—"])
    control=x["comparisons"]["B/A"]["totals"]
    sorted_pair=x["comparisons"]["Q/O"]["totals"]
    finding=paragraph(f"При обычном порядке полная цепочка совпала в {control.get('same_full',0)} из {control.get('pairs',0)} пар, "
          f"при сортировке в {sorted_pair.get('same_full',0)} из {sorted_pair.get('pairs',0)}. "
          f"Из {control.get('same_path_order_drift',0)} расхождений входа при равном следе состояний простая перестановка объяснила "
          f"{control.get('reorder_explains',0)} полных цепочек. Хеши чувствительны к именам и структуре вложенных термов.")
    return section("Перестановка верхнеуровневых SMT выражений",
         paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Блоков {x['blocks']} из {x['planned']}, JVM {x['jvms']}. "
                   "A/B передают обычный порядок, O/Q сортируют утверждения и допущения перед отправкой в cvc5. "
                   "Сравнение мультимножеств сохраняет кратности и структуру каждого терма. Точное время сравнивается на общем префиксе наблюдаемых операций.")+finding+
         tbl(("Пара","Доступно","Пар без check","Пути равны","Вход равен","Мультимножества равны","Полная цепочка равна",
              "Объяснено перестановкой","Префикс входа","Префикс мультимножеств","Check на точном префиксе"),rows)+
         tbl(("Плечо / A","Пути","Рёбра","Шаги","Δ покрытия, п.п.","PAR2","JVM wall","Нарушения"),coverage))

def live_resource():
    x=read("live-resource/evidence/summary.json")
    pre=read("live-resource/evidence/calibration-summary.json")
    if not x and pre:
        rows=[[key,pct(r["paths_ratio"]),pct(r["steps_ratio"]),f(r["coverage_delta_pp"]),pct(r["par2_ratio"]),
               r["totals"]["solver_unknown"],r["reference"]["solver_unknown"],
               ", ".join(GATES.get(k,k) for k in r["violations"]) or "—"] for key,r in pre["proof"].items()]
        return section("Калибровка ресурсного лимита в живом клиенте",
          paragraph(f"{dt(pre['start'])} — {dt(pre['end'])}. {pre['blocks']} из {pre['planned']} блоков, {pre['jvms']} JVM. "
                    f"Выбор зафиксирован {dt(pre['locked'])}. Ресурс {pre['chosen'] if pre['chosen'] is not None else 'default'}.")+
          tbl(("Ресурс","Пути","Шаги","Δ покрытия, п.п.","PAR2","UNKNOWN варианта","UNKNOWN A","Нарушения"),rows))
    if not x:
        return ""
    calibration=[[key,pct(r["paths_ratio"]),pct(r["steps_ratio"]),f(r["coverage_delta_pp"]),pct(r["par2_ratio"]),
             ", ".join(GATES.get(k,k) for k in r["violations"]) or "—"] for key,r in x["calibration"].items()]
    rows=[]
    for k,r in x["records"].items():
        ctrl=r["totals"]["A"]
        tr=r["totals"][k.split("-")[1]]
        rows.append([k,r["n"],r["aggregate_equivalent"],r["identical_trace"],result_ci(r["ratio"]["par2_ms"]),
            result_ci(r["ratio"]["paths"]),result_ci(r["prefix_ratio"]),f'{tr.get("solver_timeouts",0)}/{ctrl.get("solver_timeouts",0)}',
            f'{tr.get("other_unknown",0)}/{ctrl.get("other_unknown",0)}',f'{tr.get("step_errors",0)}/{ctrl.get("step_errors",0)}'])
    choice=short_option({"reproducible-resource-limit":str(x["diagnostic_tested"])})
    chosen=(f"После калибровки принят {choice}." if x["choice"] is not None else
            f"Калибровка сохранила default. Отдельный диагностический вариант применяет {choice}.")
    return section("Лимит ресурса в живом USVM",
         paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Параметр заморожен {dt(x['lock'])}, тест начат {dt(x['test_start'])}. "
             f"{chosen} Тест содержит {len(x['projects'])} проектов, {x['blocks']} блоков, {x['jvms']} JVM и {x['jvm_failures']} сбоев. "
             "A/B используют default, R/S — один вектор с лимитом. S сравнивается с R.")+
         tbl(("Лимит","Пути","Шаги","Δ покрытия, п.п.","PAR2","Нарушения"),calibration)+
         tbl(("Пара","Методов","Счётчики равны","Трасса равна","PAR2","Пути","Check на префиксе","Таймауты","Прочий UNKNOWN","Ошибки шага"),rows))

def resource_live_mini(folder="resource-live-mini"):
    x=read(folder+"/evidence/summary.json")
    if not x:
        return ""
    rows=[]
    for a,r in x["comparisons"].items():
        l,b=(r["totals"][k] for k in (a,"A"))
        rows.append([a,short_option(x["options"][a]),r["n"],r["identical_trace"],
               result_ci(r["prefix_ratio"]),result_ci(r["ratio"]["par2_ms"]),result_ci(r["ratio"]["paths"]),
               f((l.get("coverage",0)-b.get("coverage",0))/r["n"]) if r["n"] else "—",
               *[f'{l.get(k,0)}/{b.get(k,0)}' for k in ("steps","solver_timeouts","other_unknown","step_errors","missing")]])
    reasons=[[a,k,v] for a,r in x["unknown_reasons"].items() for k,v in r.items()]
    title="Короткий живой перенос ресурсного предела" if folder=="resource-live-mini" else "Более высокий ресурсный предел в живом клиенте"
    return section(title,
       paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Проектов {len(x['projects'])}, блоков {x['blocks']} из {x['planned']}, "
            f"JVM {x['jvms']}, сбоев {x['failures']}. 1500 шагов и solver wall 1,6 с общие. Ресурс ограничивает R.")+
       tbl(("Плечо","Опции","Методов","Полная трасса равна","Check на префиксе","PAR2","Пути","Δ покрытия, п.п.",
            "Шаги","Таймауты","Прочий UNKNOWN","Ошибки шага","Пропуски"),rows)+
       (tbl(("Плечо","Причина UNKNOWN","Число"),reasons) if reasons else ""))

def kernel_results():
    x=read("kernels/evidence/summary.json")
    if not x or "paired_ratios" not in x:
        return ""
    values=[]
    for arm,r in x["paired_ratios"].items():
        values.append([arm,short_option(x["arms"][arm]),r["n"],r["work_equal"],result_ci(r["all"]["costs"]),
                       result_ci(r["equal"]["check"]),result_ci(r["all"]["paths"])])
    per=[]
    for k,r in x["summary"].items():
        group,arm,program=k.split("|")
        per.append([group,program,arm,r["jvms"],r["valid_all"],r["wrong"],r["witness_errors"],r["solver_timeouts"],
                    r["other_unknown"],r["step_errors"],r["jvm_failed"]])
    return section("Независимые Java вычисления с оракулом",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Шесть семейств арифметики и локальных массивов, 24 класса, {x['jvms']} JVM. "
          "Независимый Java перебор даёт все классы возвращаемых значений. В каждом свидетельстве пользовательский метод реально исполнен. "
          "Сопоставимые пары имеют равные итоговые счётчики и совпавшие полные множества Java исходов. Настоящий порядок SMT операций здесь отдельно не проверялся.")+
        tbl(("Вариант","Опции","Пар","Счётчики и исходы равны","PAR2 / A","Check на сопоставимых / A","Пути / A"),values)+
        "<details><summary>Полнота и правильность по программам</summary>"+tbl(("Семейство","Класс","Плечо","Повторов","Полных",
                "Неверных","Ошибки свидетеля","Таймаут","Прочий UNKNOWN","Исключение шага","Сбой JVM"),per)+"</details>")

def ast_shadow():
    x=read("ast-shadow/evidence/summary.json")
    if not x:
        return ""
    perf=[]
    for arm,t in x["ratios"].items():
        for k,r in t.items():
            perf.append([arm,short_option(x["options"][arm]),k,r["n"],result_ci(r["cpu"]),result_ci(r["wall"])])
    counts=[[a,*[t.get(k,0) for k in ("sessions","done","expected","SAT","UNSAT","UNKNOWN","missing","correct","wrong","errors","skipped_models_or_cores")]]
            for a,t in x["checks"].items()]
    extra=read("ast-shadow/evidence/theory-results.json")
    diagnosis=""
    if extra:
        strata=[]
        for key,c in sorted(extra["counts"].items()):
            arm,theory=key.split("|",1)
            r=extra["timings"].get(key)
            strata.append([arm,theory,*[c.get(q,0) for q in ("SAT","UNSAT","UNKNOWN")],r["n"] if r else 0,
                           result_ci(r["cpu"]) if r else "—"])
        diagnosis="<details><summary>Статусы, причины UNKNOWN и времена по наблюдаемой теории</summary>"+tbl(
               ("Плечо","Состав","SAT","UNSAT","UNKNOWN","Сопоставимых","CPU / A"),strata)+tbl(
               ("Плечо","Причина UNKNOWN","Число"),[[a,key,n] for a,c in extra["reasons"].items() for key,n in sorted(c.items())])+"</details>"
    return section("Проверка одной входной последовательности объектов KSMT",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. {x['programs']} независимых проектов и семейств, "
            f"JVM {x['jvms']} из {x['planned']}, сбои JVM {x['jvm_failed']}. Заводской cvc5 управлял живым USVM. "
            "После живого анализа и до закрытия его контекста каждый альтернативный solver получил тот же упорядоченный набор объектов и вызовов. "
            "Парсер SMT-LIB не использовался. Для скорости сопоставляем ответы, которые также подтвердил Z3. Лимит отложенного check 250 мс.")+
        tbl(("Вариант","Точные опции","Группа","Сопоставимых check","Thread CPU / A","Check wall / A"),perf)+
        tbl(("Вариант","Потоков","Полных","Ожидалось check","SAT","UNSAT","UNKNOWN","Пропуски","Согласие с Z3",
             "Расхождения с Z3","Ошибки","Пропуски моделей или ядер"),counts)+
        "<details><summary>Состав входов KSMT</summary>"+
        tbl(("Группа","Наблюдаемые теории","Запросов"),[[kind,theory,n] for kind,c in x["theories"].items()
              for theory,n in sorted(c.items())])+"</details>"+diagnosis)

def array_semantics():
    x=read("array-semantics/evidence/summary.json")
    if not x or "contrasts" not in x:
        return ""
    arm_rows=[]
    for arm,r in x["contrasts"].items():
        arm_rows.append([arm,short_option(x["options"][arm]),r["control"],r["n"],r["matched"],result_ci(r["par2"]),
                         result_ci(r["matched_check"]),result_ci(r["matched_wall"])])
    rows=[]
    for family,arms in x["families"].items():
        for arm,c in arms.items():
            rows.append([family,arm,f'{c.get("complete",0)}/{c.get("n",0)}',
                         f'{c.get("correct",0)}/{c.get("expected",0)}',
                         *[c.get(k,0) for k in ("store_all","timeout","other_unknown","wrong","witness_errors","step_errors","jvm_failed")]])
    return section("Константные массивы и достижимые результаты Java",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. {x['blocks']} блоков из {x['planned']}, {x['jvms']} JVM. "
            "В опыте 11 обычные overwrite методы попадали в ответ UNKNOWN на STORE_ALL. Здесь проверено причинное вмешательство arrays-exp=true, "
            "две его комбинации, два запуска default и Z3 на шести новых семействах. Для каждого достигнутого состояния Java проверяет конкретный свидетель.")+
        tbl(("Плечо","Точные опции cvc5","Сравнение","Пар","Полный оракул и те же счётчики","PAR2","Check в сопоставимых","Wall в сопоставимых"),arm_rows)+
        tbl(("Семейство","Плечо","Программ в повторах полных","Классов достигнуто","STORE_ALL","Таймаут","Прочий UNKNOWN",
             "Неверные свидетели","Ошибки свидетелей","Ошибки шага","Сбои JVM"),rows))

def native_interactions():
    x=read("interactions/evidence/summary.json")
    g=read("native-search/evidence/gate-interactions.json")
    if not x:
        return ""
    rows=[]
    for a,groups in x["results"].items():
        for d in groups:
            t=d["totals"]
            rows.append([a,d["family"] or "Все",d["programs"],d["blocks"],short_option(x["option_sets"][a]),
                result_ci(d["ratios"]["cpu_known_ms"]),result_ci(d["ratios"]["par2_ms"]),
                "/".join(map(str,t["correct"])),"/".join(map(str,t["incorrect"]))])
    text=paragraph(f"{dt(x['start'])} — {dt(x['end'])}. У {x['oracle_passed']} программ есть независимые точные ответы. "
                     f"{x['blocks']} блоков и {x['jvms']} JVM. Два эффективных переключателя и их сочетание измерены на новых параметрах.")
    if g:
        text+=paragraph(f"Решение допуска принято {dt(g['at'])}. Порог прошли {g['heads']} варианта из трёх.")
    return section("Репликация BV опций и взаимодействия",text+
        tbl(("Вектор","Семейство","Программ","Блоков","Точные опции","Matched CPU / A","PAR2 / A","Верные / A","Неверные / A"),rows))

def width_results():
    x=read("width-study/evidence/summary.json")
    if not x:
        return ""
    def table(results):
        rows=[]
        for r in results:
            c=r["counts"]
            rows.append([r["width"],r["arm"]+" / "+r["ref"],r["expected"],r["n"],r["matched"],
                 result_ci(r["ratios"]["cpu"]),result_ci(r["ratios"]["par2"]),
                 *["/".join(map(str,c[k])) for k in ("correct","timeout","unknown","incorrect","missing","solver_error")]])
        return tbl(("Бит","Точные векторы","Исход оракула","Пар","Обе верны","Matched CPU","PAR2","Верные","Таймаут",
                    "UNKNOWN","Неверные","Пропуски","Ошибки"),rows)
    shared=[r for r in x["results"] if r["expected"]=="all" and r["arm"]=="gauss"]
    finding=""
    if shared:
        text="В стандартном лимите интервал matched CPU включает равенство на каждой измеренной ширине." if all(
          None not in r["ratios"]["cpu"]["ci"] and r["ratios"]["cpu"]["ci"][0]<=1<=r["ratios"]["cpu"]["ci"][1] for r in shared) else ""
        finding=paragraph(text) if text else ""
    return section("Размер BV и проверка на границе таймаута",
       paragraph(f"{dt(x['start'])} — {dt(x['end'])}. {x['blocks']} из {x['planned']} блоков, JVM {x['jvms']} и сбоев {x['jvm_failed']}. "
           "На разных ширинах сохраняются те же четыре алгебраические схемы. Интервалы используют схемы как кластеры. "
           "Короткий и обычный пределы сравнивают опции внутри своего предела.")+finding+
       tbl(("Вектор","Опции"),[[a,short_option(c)] for a,c in x["options"].items()])+
       tbl(("Бит","Независимый Z3 оракул"),[[w,f'{k[0]}/{k[1]}'] for w,k in x["oracle"].items()])+
       table(r for r in x["results"] if r["expected"]=="all")+
       "<details><summary>Разбивка по SAT и UNSAT</summary>"+table(r for r in x["results"] if r["expected"]!="all")+"</details>")

def array_search():
    x=read("array-tuning/evidence/summary.json")
    g=read("array-tuning/evidence/gate.json")
    if not g and not x:
        return ""
    proof=tbl(("Вектор 18","Семейств с приростом полных наблюдений","Прирост в повторах","Проблемы","Допуск"),
              [[a,sum(n>0 for n in r["gains"].values()),sum(r["gains"].values()),r["issues"],"Да" if r["passed"] else "Нет"]
               for a,r in g["results"].items()])
    if not x:
        return section("Допуск поиска на задачах с массивами",paragraph(f"{dt(g['at'])}. Допуск {'пройден' if g['passed'] else 'не пройден'}.")+proof)
    choices=[]
    trials=[]
    for name, r in x["choices"].items():
        s=r["search"]
        v=r["validation"]
        total=(datetime.fromisoformat(s["end"])-datetime.fromisoformat(r["train_start"])).total_seconds()
        validation_seconds=(datetime.fromisoformat(v["end"])-datetime.fromisoformat(v["start"])).total_seconds()
        choices.append([name,dt(r["train_start"]),dt(s["end"]),f(total,1),f(s["inference_s"],1),s["evaluations"],s["rejected"],
                        dt(v["start"]),dt(v["end"]),f(validation_seconds,1),len(v["checked"]),
                        short_option(r["options"]),x["labels"][name]])
        for record in s["records"]:
            estimate=record.get("result",{})
            if "sol" in name or estimate:
                trials.append([name,record["attempt"],record.get("mask","—"),f(record.get("inference_s"),1),
                  estimate.get("gain","—"),f(estimate.get("cost")),
                  ", ".join(estimate.get("violations",[])) or "—",record.get("reason","")])
    rows=[]
    for name, cfg in x["options"].items():
        if name=="A":
            continue
        totals=[r[name]["totals"] for r in x["test"]]
        attrs=("complete_outcomes","known_outcomes","retrieved_outcomes","unknown","store_all","solver_timeouts","wrong","jvm_failed")
        rows.append([name,short_option(cfg),*["/".join(str(sum(t[i][k] for t in totals)) for i in (0,1)) for k in attrs],
                     result_ci(x["ratios"][name])])
    comparison=read("array-tuning/evidence/comparison.json")
    against=""
    if comparison:
        direct=[]
        for label, c in comparison["comparisons"].items():
            d=c["full_delta"]
            limits=([pct(d["value"])," – ".join(pct(n) for n in d["ci"])] if d else ["—","—"])
            direct.append([label,*limits,c["matched"],result_ci(c["times"])])
        against=tbl(("Пара","Δ доли полных исходов","95% по семьям","Сопоставимых","Check на сопоставимых"),direct)
        failures=comparison.get("failures",{}).get("counts",{})
        if failures:
            against+="<details><summary>Остановленные и сбойные JVM при поиске</summary>"+tbl(
                ("Набор","Причина","Последнее событие","JVM"),[[*k.split("|"),n] for k,n in failures.items()])+"</details>"
        if comparison.get("anytime"):
            against+="<details><summary>Темп нахождения допустимого вектора на train</summary>"+tbl(
                ("Стратегия","Первый прирост ≥2, с","Прирост к 120 с","Прирост к 180 с","Прирост к 240 с"),
                [[r["arm"],f(r["first_gain"],1),*[r["gain_at"][t] for t in ("120","180","240")]] for r in comparison["anytime"]])+"</details>"
    return section("Сравнение стратегий на живых задачах с массивами",
       paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Выбор зафиксирован {dt(x['lock'])}, тест начат {dt(x['test_start'])}. "
            f"Закрытая часть включает {x['families']} независимых семейств и {x['test_jvms']} JVM. "
            "Sol использует изолированные измеренные train сводки. A проходит во всех блоках как заводской cvc5, E как правило arrays-exp=true. "
            "Равные векторы используют общие наблюдения. Сопоставимые времена ограничены точными исходами конкретного оракула и равными измеренными счётчиками.")+
       paragraph("В каждом раунде каждой стратегии выделено 270 с стенового времени на train с контрольными A/B, оценками и ожиданием ответа Sol. "
                 "На validation выделено 140 с и до двух кандидатов по одинаковым критериям. Фактическое время и число оценок приведены ниже. "
                 "По четыре разных семейства целиком отнесены к train, validation и test до начала измерений.")+
       proof+tbl(("Раунд и стратегия","Train начало","Train конец","Train с A/B, с","Модель, с","Новых оценок","Отказов",
                  "Validation начало","Validation конец","Validation, с","Кандидатов","Выбор","Вектор теста"),choices)+
       "<details><summary>Предложения и измерения train</summary>"+tbl(("Стратегия","Попытка","Маска","Модель, с","Δ полных",
                  "Стоимость","Причины отказа","Обоснование Sol"),trials)+"</details>"+
       tbl(("Вектор","Опции","Полных / A","Категорий ожидалось / A","Категорий найдено / A","UNKNOWN / A","STORE_ALL / A",
            "Таймаут / A","Неверные / A","Сбои JVM / A","Check на сопоставимых / A"),rows)+against)

def array_transfer():
    x=read("array-transfer/evidence/summary.json")
    design=read("array-transfer/inputs/design.json")
    if not x:
        return section("Перенос на библиотечные массивы",paragraph(f"{dt(design['at'])}. Статический фильтр нашёл {len(design['projects'])} независимых проектов.")) if design else ""
    rows=[]
    simple=all(not any("STORE_ALL" in k for k in counts) for counts in x["unknowns"].values())
    for a,r in x["contrasts"].items():
        tr,dr=(r["totals"][s] for s in (a,"A"))
        reasons=x["unknowns"]
        detail=[sum(v for k,v in reasons[s].items() if "STORE_ALL" in k) for s in (a,"A")]
        rows.append([a,short_option(x["options"][a]),r["n"],r["identical_trace"],result_ci(r["prefix_ratio"]),
                     result_ci(r["ratio"]["paths"]),result_ci(r["ratio"]["par2_ms"]),
                     f'{tr.get("solver_timeouts",0)}/{dr.get("solver_timeouts",0)}',
                     f'{tr.get("other_unknown",0)}/{dr.get("other_unknown",0)}',f'{detail[0]}/{detail[1]}',
                     f'{tr.get("step_errors",0)}/{dr.get("step_errors",0)}'])
    return section("Перенос настройки массивов на библиотеки",
         paragraph(f"{dt(x['start'])} — {dt(x['end'])}. {len(x['projects'])} проектов, {x['blocks']} блоков из {x['planned']}, "
                   f"{x['jvms']} JVM и {x['fails']} сбоев. Методы отобраны до динамики по созданию массива, записи и чтению в байткоде.")+
         (paragraph("В пилоте ни один вариант не встретил STORE_ALL. Условия воспроизведения отказа зависят от состояний и выражений, которых достиг USVM.") if simple else "")+
         tbl(("Плечо","Опции","Пар","Полная трасса равна","Check по общему префиксу","Пути","PAR2","Таймауты",
              "Прочий UNKNOWN","STORE_ALL","Ошибки шага"),rows))

def prompt_study():
    x=read("prompt-study/evidence/summary.json")
    if not x:
        return ""
    rows=[]
    for r in x["calls"]:
        response=r["answer"] or {}
        rows.append([r["job"],r["arm"],dt(r["status"]["start_msk"]),f(r["status"]["wall_ms"]/1000,1),
            ", ".join(response.get("options",[])) or "—",r["tool_events"],response.get("reason",r["status"]["watchdog"])])
    a,b=(x["by_arm"][k] for k in ("A","B"))
    return section("Понимание значений cvc5 по умолчанию в Sol",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Числовых train пакетов {x['source_packets']}. "
              f"Из {a['answers']} ответов A и {b['answers']} ответов B вариант arrays-exp попал в {a['include_arrays_exp']} и {b['include_arrays_exp']} соответственно. "
              "A применяет исходный вход. B дополнительно указывает измеренные значения по умолчанию и смысл переопределения. "
              "Одинаковые train пакеты, новые запросы и малая описательная выборка.")+
        tbl(("Train пакет","Формат","Начало","Секунд","Предложение","Инструментов","Обоснование"),rows))

def smac_recovery():
    t=read("smac-recovery/inputs/repair.json")
    if not t:
        return ""
    text=paragraph(f"Правила восстановления зафиксированы {dt(t['at'])} до исходной заморозки теста. "
           "SMAC 2.4.0 потерял запрещающую маску в копии ConfigSpace для случайной инициализации. "
           "Контроллер отклонил запрещённый первый ask третьего раунда и завершил запуск ещё до оценки. Исходный результат сохранён.")
    x=read("smac-recovery/evidence/summary.json")
    if x:
        src=("Использованы уже запечатанные измерения такого же точного вектора. Число независимых наблюдений осталось прежним."
             if x["test_reused"] else "Новый точный вектор измерен отдельно в полном блоке с A и E.")
        text+=paragraph(f"{dt(x['start'])} — {dt(x['end'])}. В бюджете учтено {f(x['charged_seconds'],1)} с исходного A/B и инициализации. "
           f"Выполнено {x['search']['evaluations']} ранее незапущенных оценок. Выбор {short_option(x['choice']['options'])} заморожен "
           f"{dt(x['choice']['at'])} до чтения закрытых исходов скриптом восстановления. {src}")
        rows=[]
        for ref, rep in x["test"].items():
            rows.append([ref,*["/".join(str(sum(r["totals"][i][k] for r in rep)) for i in (0,1)) for k in
                          ("complete_outcomes","unknown","wrong","jvm_failed")]])
        if rows:
            text+=tbl(("Опорный вектор","Полные","UNKNOWN","Неверные","Сбои JVM"),rows)
    return section("Восстановление инициализации SMAC",text)

def strategy_searches():
    stages=(("Нативное сравнение стратегий","native-search/evidence/summary.json"),
            ("Сравнение в живом клиенте","search/evidence/live-summary.json"),
            ("Сравнение в свежих JVM","search/cold/evidence/live-summary.json"))
    reported=""
    for title,file in stages:
        x=read(file)
        if not x:
            continue
        choices=x.get("choices",x.get("arms",{}))
        rows=[]
        for name,r in choices.items():
            engine=r.get("arm",r.get("search"))
            rows.append([name,dt(engine["start"]),dt(engine["end"]),f(engine["wall_s"],1),f(engine["inference_s"],1),
                engine["evaluations"],engine["rejected"],short_option(r["options"]),x["labels"][name]])
        test_rows=[]
        for name,stat in x["contrasts"].items():
            if "ci" in stat:
                test_rows.append([name,short_option(x["configs"][name]),result_ci(stat),"—","—"])
            else:
                test_rows.append([name,short_option(x["configs"][name]),result_ci(stat["par2"]),
                                 result_ci(stat["check"]),result_ci(stat["paths"])])
        source=x.get("lock",x.get("locked"))
        reported+=section(title,paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Выбор зафиксирован {dt(source)} до измерения закрытой части.")+
            tbl(("Стратегия и повтор","Первый поиск","Последний поиск","Секунд","Модель, с","Оценок","Отказов","Опции","Вектор теста"),rows)+
            paragraph("Совпавшие векторы используют одно тестовое измерение на блок. Плечо A — default.")+
            (tbl(("ID","Опции","PAR2 / A","Check / A","Пути / A"),test_rows) if test_rows else
             paragraph("На тесте присутствует только заводской вектор.")))
    return reported

def panel_results():
    p=read("library-panel/evidence/summary.json")
    if not p:
        return ""
    rows=[]
    for name,r in p["contrasts"].items():
        nd=name.split("-")[1]
        c=r["ratio"]
        rows.append([name,short_option(p["opts"][nd]),r["n"],r["aggregate_equivalent"],r["identical_trace"],
          result_ci(c["par2_ms"]),result_ci(c["analyze_ms"]),result_ci(c["check_ms"]),result_ci(c["paths"])])
    metrics=[]
    for mode in ("wall","fixed"):
        for arm in ("B","P","X"):
            trace=[p["contrasts"][f"{mode}-{arm}-{label}"] for label in ("plain","trace")]
            n=sum(x["n"] for x in trace)
            a,b=[{k:sum(r["totals"][tag].get(k,0) for r in trace) for k in ("paths","edges","steps","coverage","checks",
              "other_unknown","solver_timeouts","step_errors","missing","failed")} for tag in (arm,"A")]
            metrics.append([mode,arm,n,*[f'{a[k]}/{b[k]}' for k in ("paths","edges","steps","checks")],
                 f((a["coverage"]-b["coverage"])/n) if n else "—",
                 *[f'{a[k]}/{b[k]}' for k in ("other_unknown","solver_timeouts","step_errors","missing","failed")]])
    q=read("library-panel/evidence/precision.json")
    precision=""
    if q:
        noise=[]
        doses=[]
        for mode, r in q["results"].items():
            for field,x in r["power"].items():
                noise.append([mode,field,x["projects"],f(x.get("a_a_geometric")),f(x.get("sd_log")),
                              *[pct(x.get("power",{}).get(w)) for w in ("0.02","0.05","0.1")]])
            k=r["triple"]
            doses.append([mode,k["n"],k["dose"]["clusters"],result_ci(k["dose"]),result_ci(k["time"])])
        precision=(paragraph("Иллюстративная мощность по 10 000 повторам использует центрированные проектные логарифмы A/A и односторонний биномиальный порог. "
                   "Доза сравнивается со средним двух default в одной тройке. Совпадение трасс требуется в трассированной части, обычная часть использует счётчики.")+
            tbl(("Бюджет","Метрика","Проектов","Геометрия A/A","σ логарифма","2%","5%","10%"),noise)+
            tbl(("Бюджет","Троек","Проектов","Выявлено / доза","Check P / среднее A/B"),doses))
        if q.get("candidate"):
            controls=[]
            for mode,r in q["candidate"].items():
                g=r["comparison"]
                controls.append([mode,result_ci(r["ratios"]["par2_ms"]),pct(g["paths_ratio"]),f(g["coverage_delta_pp"]),
                     g["totals"]["solver_unknown"],g["reference"]["solver_unknown"],r["exact_count"],result_ci(r["exact_check"]),
                     ", ".join(GATES.get(k,k) for k in g["violations"]) or "—","Да" if r["passed"] else "Нет"])
            precision+=tbl(("Бюджет X","Полный PAR2","Пути","Δ покрытия, п.п.","UNKNOWN X","UNKNOWN A","Точных пар",
                "Check точных пар","Нарушения","Допуск"),controls)
    return section("Расширенная панель реальных библиотек",
         paragraph(f"{dt(p['start'])} — {dt(p['end'])}. Проектов {len(p['projects'])}, "
             f"блоков {p['completed']} из {p['planned']}, JVM {p['jvms']}, сбоев {p['failed']}.")+
         tbl(("Контраст","Опции","Пар","Счётчики равны","Полная трасса равна","PAR2","Анализ","Check","Пути"),rows)+precision+
         "<details><summary>Работа и надёжность в сравнении с A</summary>"+
         tbl(("Бюджет","Плечо","Методов","Пути","Рёбра","Шаги","Checks","Δ покрытия, п.п.","Прочий UNKNOWN","Таймауты",
              "Ошибки шага","Пропуски","Штрафные методы"),metrics)+
         paragraph("У произвольных библиотечных методов сверены состояние, траектория и статусы. Число ошибочных SAT или UNSAT ответов требует отдельного независимого оракула.")+"</details>")

def encoding_trial():
    x=read("fp-encoding/evidence/summary.json")
    if not x:
        return ""
    rows=[]
    for k,r in sorted(x["summary"].items()):
        project,arm,kind=k.split("|")
        rows.append([project,arm,kind,r["jvms"],r["valid_all"],r["categories"],r["wrong"],r["witness_errors"],
                     r["step_errors"],r["solver_timeouts"],r["other_unknown"],r["jvm_failed"]])
    proto=[r for k,r in x["summary"].items() if "|prototype|raw" in k]
    pwrong=sum(r["wrong"] for k,r in x["summary"].items() if k.startswith("payload|prototype|"))
    zwrong=sum(r["wrong"] for k,r in x["summary"].items() if k.startswith("payload|z3|"))
    finding=paragraph(f"Прототип достиг всех ожидаемых категорий обычного raw в {sum(r['valid_all'] for r in proto)}/{sum(r['jvms'] for r in proto)} наблюдениях. "
                        f"Контроль NaN payload обнаружил {pwrong} ложных конкретных свидетелей у прототипа и {zwrong} у Z3. "
                        "Измеренная полнота правильных категорий и число ложных свидетельств учитываются отдельно.")
    return section("Экспериментальное FP кодирование в клиенте",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Выполнено {x['jvms']} JVM на пяти группах IEEE программ. "
          "С тем же cvc5 проверена замена прямого преобразования на свежий BV и равенство восстановленного FP оригиналу. "
          "Обе конфигурации cvc5 имеют заводские опции. Отдельная проверка тихого NaN чувствительна к полезной нагрузке. "
          "В записях указана проверка конкретного свидетеля на физическом JVM.")+finding+
        tbl(("Программа","Рантайм","Форма","Повторов","Полных","Сумма категорий","Неверные","Ошибка свидетеля",
             "Ошибка шага","Таймаут","Прочий UNKNOWN","Сбой JVM"),rows))

def cpu_control():
    x=read("cpu-dose/evidence/summary.json")
    if not x:
        return ""
    rows=[[k,result_ci(r),r["clusters"]] for k,r in x["measurements"].items()]
    rows.extend([["A/A "+k,result_ci(r),r["clusters"]] for k,r in x["aa"].items()])
    c=x["cases"]
    return section("Измерение известной дозы CPU",
        paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Из {c.get('methods',0)} парных методов "
          f"точно совпавшая A/A есть у {c.get('AA_match',0)}, точная A/A/P у {c.get('P_match',0)}. "
          "Метрика CPU измеряет поток, который сейчас выполняет check. В P учтены отдельные 2 мс CPU перед check.")+
        tbl(("Оценка","Отношение и 95% интервал","Кластеров"),rows))

def trace_overhead():
    x=read("trace-overhead/evidence/summary.json")
    if not x:
        return ""
    timed=[[a,r["n"],result_ci(r["check"]),result_ci(r["analyze"])] for a,r in x["matched"].items()]
    quality=[[a,*[r[k] for k in ("paths","steps","checks","solver_unknown","step_errors","jvm_failed")]] for a,r in x["quality"].items()]
    return section("Влияние записи трассы на измерение",
       paragraph(f"{dt(x['start'])} — {dt(x['end'])}. Завершено {x['blocks']} из {x['planned']} блоков, {x['jvms']} JVM. "
          "A/B без трассировки, T с SHA цепочкой, у всех заводские опции. Сравнение времени требует совпадения рабочих счётчиков.")+
       tbl(("Плечо / A","Совпавших по счётчикам","Check","Время анализа"),timed)+
       tbl(("Плечо","Пути","Шаги","Checks","UNKNOWN","Ошибки шага","Сбои JVM"),quality))

def solver_unknowns(include_fresh=True):
    x=read("diagnostics/evidence/solver-unknowns.json")
    if not x:
        return ""
    labels={"selection":"Исходные train и validation","test-pre":"Тест до перезагрузки",
            "test-post":"Тест после перезагрузки","arithmetic-java":"Свежие Java задачи 11"}
    rows=[]
    for group, arms in x.items():
        if group=="arithmetic-java" and not include_fresh:
            continue
        for a,d in arms.items():
            c=d["all"]
            rows.append([labels[group],a,*[c.get(k,0) for k in
                         ("methods","checks","TIMEOUT","STORE_ALL","methods_STORE_ALL","RESOURCEOUT","other")]])
    text="Отказ STORE_ALL наблюдался в закрытом тесте на семействах commons-validator, при этом ни один сохранённый исходный train и validation такого ответа не содержал. Три cvc5 на тесте имеют одинаковые опции."
    if include_fresh:
        text+=" Отдельный Java-оракул показал, что код с индексированными записями в константный массив может терять достижимые результаты из-за такого UNKNOWN."
    return section("Причины ответов UNKNOWN",paragraph(text)+
          tbl(("Страта","Плечо","Методов","Check","Таймаут","STORE_ALL","Методов с STORE_ALL","Ресурсный предел","Прочие"),rows))

def acquisition_close():
    end=read("evidence/acquisition-stop.json")
    if not end:
        return ""
    start=read("evidence/session.json")
    audit=read("evidence/post-close-audit.json")
    audit_note=(paragraph(f"В {dt(audit['at'])} архивные файлы повторно сверены с исходной кампанией. "
        "Итог закрытого теста с массивами независимо пересчитан из сырых Java-свидетельств и замороженного оракула. "
        "Подробности сохранены в evidence/post-close-audit.json.") if audit else "")
    return section("Закрытие окна",paragraph(f"Начало окна {dt(start['requested_start'])}. Первая сверка хоста {dt(start['first_verified_host_clock'])}. "
        f"Последний записанный запуск завершился {dt(end['last_native_or_jvm'])}. Блокировка сбора свободна по проверке {dt(end['observed'])}. "
        f"Свободно {f(end['free_bytes']/1e9,2)} ГБ. Верификация использует сохранённые измерения.")+audit_note)

def closing_calibration():
    x=read("closing-calibration/evidence/summary.json")
    if not x:
        return ""
    rows=[]
    for core, modes in x["summary"].items():
        for mode,r in modes.items():
            rows.append([core,mode,r["n"],f(r["cpu_ms"]["old"],1),f(r["cpu_ms"]["new"],1),
                f(r["wall_ms"]["old"],1),f(r["wall_ms"]["new"],1)])
    return section("Контроль нагрузки при завершении",paragraph(f"{dt(x['start'])} — {dt(x['end'])}. {x['measured']} из {x['planned']} измерений. "
      f"Сбоев {x['failed']}. Начальная контрольная отметка {dt(x['previous'])}. SHA вычислялся тем же кодом в разных временных эпохах. "
      "Медианы описывают дрейф хоста и не изменяют оценки SMT.")+
      tbl(("Ядро","Режим","Конечных повторов","CPU нач. мс","CPU конец мс","Wall нач. мс","Wall конец мс"),rows))

def main():
    recovery_methods=paragraph("Сверены запечатанный вход, расписание, решения, SHA-256 JAR и JNI, /proc/self/maps, фактические опции cvc5 до и после первого check, seed и лимиты USVM. cvc5 1.4.1, Z3 4.13.0.0, KSMT 0.5.26, Java 21.0.12.1+1. Неполные архивные блоки сохранены побайтно в quarantine и исключены из оценки. PAR2 равен полному времени метода вне штрафных событий. За ошибку метода или шага, UNKNOWN, останов по времени либо пропуск начисляются 16 с.")
    loc=paragraph("Данные находятся в recovery/evidence и recovery/raw. В inputs находятся запечатанные расписание, манифест и выбор. Для сверки запустить recovery/scripts/diagnose.py, recovery/scripts/recover.py и recovery/scripts/summarize.py существующим Python ../.venv/bin/python с PYTHONDONTWRITEBYTECODE=1 и affinity 12–15. Верификация повторно использует полные блоки.")
    detail=(section("Отбор до перезагрузки",selection())+
            section("Проверка хоста после перезагрузки",calibration())+
            section("Закрытый тест",recovery_results())+solver_unknowns(include_fresh=False)+
            section("Методика и воспроизводимость",recovery_methods+loc))
    atomic(ROOT/"recovery/report.html",doc("Восстановление фиксированного теста USVM",
           "Исходный поиск 22 ноября 2026 года. Сверка и целиком повторённые блоки 23 ноября, butvinm-work.",detail))
    header=paragraph("На исходном живом эксперименте Sol и SMAC выбрали заводские настройки cvc5. Проверенные альтернативы не прошли одновременно пороги времени, качества и надёжности. Выбор заморожен до перезагрузки. В закрытом тесте у меток Sol и SMAC одинаковый cvc5.") 
    header+=paragraph("Ускорение порядка 2% в исходной схеме трудно различить. Фиксированный лимит шагов повышает сопоставимость, и совпавшие счётчики ещё требуют проверки траектории. Прямые FP→BV преобразования дают отдельный отказ адаптера. Нативные флаги могут менять смысл после первого check.")
    if read("native-search/evidence/gate-interactions.json"):
        header+=paragraph("Свежая нативная репликация на 48 программ и четырёх семействах не подтвердила перенос ускорения BV флагов с сохранением ответов. Неустойчивые эффекты и различия исходов учтены до допуска нового поиска.")
    if read("diagnostics/evidence/solver-unknowns.json"):
        header+=paragraph("У исходной поисковой выборки не было отказов STORE_ALL, которые появились на другом проекте в закрытом тесте. Контроль Java подтвердил потерю достижимых исходов. Это конкретный случай, где состав клиента и ограничения решателя меняют качество измерения.")
    if (arrays:=read("array-semantics/evidence/summary.json")) and "families" in arrays and "E" in arrays["options"]:
        a,e=[sum(v[k]["complete"] for v in arrays["families"].values()) for k in ("A","E")]
        n=sum(v["A"]["n"] for v in arrays["families"].values())
        u=sum(v["A"]["store_all"] for v in arrays["families"].values())
        header+=paragraph(f"В опытных Java задачах настройка arrays-exp=true подняла полноту исходов с {a}/{n} до {e}/{n} и устранила все {u} ответов STORE_ALL default. "
            "Это восстановление работы клиента. Разница PAR2 в первую очередь отражает исчезновение штрафов за UNKNOWN.")
    if (result:=read("array-tuning/evidence/comparison.json")):
        c=result["comparisons"]["E/A"]["counts"]
        e,a=c["complete_outcomes"]
        header+=paragraph(f"На четырёх новых закрытых семействах одиночное правило arrays-exp=true дало {e}/{result['comparisons']['E/A']['total']} "
             f"полных наблюдений против {a} у default. Две комбинации дали те же множества исходов. Интервалы времени check на сопоставимых "
             "наблюдениях включают равенство с одиночной опцией.")
        if read("smac-recovery/evidence/summary.json"):
            header+=paragraph("На этой узкой задаче Sol подтвердил полезный вектор в 3 из 3 раундов, random в 2 из 3, исходный SMAC в 1 из 3. "
                 "Третий раунд SMAC прервался до оценки кандидатов. После восстановления его результата счёт SMAC составил 2 из 3. "
                 "Результат описывает поиск в малом пространстве и не устанавливает преимущество адаптивного метода над заранее известным правилом.")
    if read("fp-encoding/evidence/summary.json"):
        header+=paragraph("Экспериментальный обход ограничения FP→BV восстанавливает обычные диагностические исходы. Тест на полезную нагрузку NaN выявил ложные Java-свидетельства, поэтому для этих операций требуется отдельная модель сохранения битов.")
    if (panel:=read("library-panel/evidence/precision.json")):
        w=panel["candidate"]["wall"]
        fxd=panel["candidate"]["fixed"]
        if not (w["passed"] or fxd["passed"]):
            header+=paragraph("На независимой библиотечной панели static-learning=false остался вне допуска. При стеновом бюджете сохранилось "
                f"{pct(w['comparison']['paths_ratio'])} путей default. При фиксированных шагах увеличилось число solver timeout. "
                "Заводская настройка остаётся опорой для широкого живого профиля. Подтверждённое условие STORE_ALL даёт отдельный сценарий для проверки arrays-exp=true.")
        header+=paragraph("Для независимого переноса использованы классы Java библиотек и отдельные вычислительные семейства с оракулами. "
                          "В локальных рабочих копиях TSA не обнаружен.")
    if (dose:=read("cpu-dose/evidence/summary.json")):
        thread=dose["measurements"]["dose_cpu"]
        wall=dose["measurements"]["dose_wall"]
        header+=paragraph(f"На {dose['cases'].get('P_match',0)} строго совпавших тройках контрольная доза дала отношение выявленного к внесённому "
              f"{result_ci(thread)} при таймере CPU потока и {result_ci(wall)} при wall. Для оценки малых различий check полезны сравнения "
              "на одинаковой работе с отдельным временем CPU. Время JVM и покрытие остаются самостоятельными критериями.")
    if (small:=read("resource-live-mini/evidence/summary.json")):
        observed=small["comparisons"]["R"]["totals"]
        header+=paragraph(f"Живой ресурсный предел 32000 дал {observed['R']['paths']} путей против {observed['A']['paths']} при заводском cvc5. "
             f"Предел добавил {observed['R']['other_unknown']} ресурсных UNKNOWN в коротком пилоте. Сокращение сырого времени сопровождалось меньшей выполненной работой.")
    if (high:=read("resource-live-hi/evidence/summary.json")):
        pair=high["comparisons"]["R"]
        observed=pair["totals"]
        header+=paragraph(f"При пределе 256000 счётчики путей сравнялись. Осталось {observed['R']['other_unknown']} ресурсных UNKNOWN. "
             f"Совпали {pair['identical_trace']} из {pair['n']} полных трасс. На точном общем префиксе отношение времени check "
             f"{result_ci(pair['prefix_ratio'])}.")
    if (logic:=read("logic-complement/evidence/summary.json")):
        header+=paragraph(f"В нативном сравнении объявление ALL меняет только доступные теории. На {logic['programs']} новых программах "
             f"CPU на совместно правильных вырос до {result_ci(logic['timing']['C']['cpu'])}. Эти потоки не воспроизводят смешанный живой вход USVM, "
             "где выбор узкой логики потребовал бы надёжной классификации всех запросов." )
    mainpage=section("Ответ на вопрос о настройке",header)+section("Восстановленный эксперимент",
        '<p>Подробности исходного отбора и калибровки включены в основной отчёт ниже. Отдельная копия сохранена в <code>recovery/report.html</code>.</p>'+
        recovery_results())+followups()+fp_adapter()+oracle_live()+encoding_trial()+native_theories()+bv_backend()+warm_screen()+live_gate()+stable_screen()+trace_cause()+order_results()+kernel_results()+array_semantics()+array_search()+smac_recovery()+prompt_study()+array_transfer()+ast_shadow()+cpu_control()+trace_overhead()+native_interactions()+width_results()+logic_study()+logic_study("logic-complement")+logic_study("logic-unused-uf")+native_budget()+pure_resource()+live_resource()+resource_live_mini()+resource_live_mini("resource-live-hi")+strategy_searches()+panel_results()+error_origins()+solver_unknowns()+time_shares()+repeat_precision()+section("Диагностика замороженного выбора",selection())+        section("Рантайм и воспроизводимость",calibration()+recovery_methods+loc)+section("Журнал кампании",progress())+closing_calibration()+acquisition_close()
    atomic(ROOT/"report.html",doc("Живая настройка cvc5 в USVM",
        "23 ноября 2026 года, 03:26:11–09:26:11 МСК · butvinm-work · сбор данных до 09:21 МСК",mainpage))

if __name__=="__main__":
    main()
