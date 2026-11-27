"""Build an autonomous Russian report from the verified live run."""
from datetime import datetime
from html import escape
from pathlib import Path

from common import ROOT, ARMS, TZ, read, sha

RAW = ROOT/"runs/primary"
LABEL = {"z3":"Z3 default","cvc5-default":"cvc5 1.4.1 default","cvc5-none":"cvc5 1.4.1 none"}
D = "cvc5-none/cvc5-default"
PAIRS = {D:"cvc5 none / cvc5 default", "cvc5-default/z3":"cvc5 default / Z3", "cvc5-none/z3":"cvc5 none / Z3"}

def stamp(iso):
    return datetime.fromisoformat(iso.strip()).astimezone(TZ).strftime("%d.%m.%Y %H:%M:%S.%f")[:-3]

def f(value, digits=3):
    if value is None:
        return "—"
    return f"{value:,.{digits}f}".replace(",", "\u202f").replace(".", ",")

def num(n):
    return f"{n:,}".replace(",", "\u202f")

def interval(x, digits=3):
    if x["value"] is None:
        return "—"
    return f"{f(x['value'],digits)} [{f(x['ci'][0],digits)} · {f(x['ci'][1],digits)}]"

def conv(value):
    if value is None:
        return "—"
    return f(value/1000,3)

def tab(headers, rows, caption=None):
    header = "".join(f'<th scope="col">{escape(str(v))}</th>' for v in headers)
    body = "".join("<tr>"+ "".join(f"<td>{escape(str(v))}</td>" for v in row) +"</tr>" for row in rows)
    label = f"<caption>{escape(caption)}</caption>" if caption else ""
    return f'<div class="table"><table>{label}<thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'

def code(s):
    return f"<code>{escape(str(s))}</code>"

def effect(r, relevant):
    lo,hi = r["ci"]
    if not (lo is not None and hi is not None):
        return "Оценке не хватает данных."
    if hi < 1:
        if relevant:
            return "Штрафное время сократилось при изменившемся объёме поиска."
        return "Данные указывают на сокращение штрафного времени при сопоставимом зафиксированном объёме поиска."
    if lo > 1:
        return "Данные указывают на рост штрафного времени."
    return "Интервал включает равенство и допускает выигрыш и проигрыш."

def timeline(metrics, verified):
    rows = [
        ["Начало работы на хосте",stamp((ROOT/"evidence/session-start-msk.txt").read_text()),"","Чтение протоколов, проверка хоста"],
        ["Предрегистрация",stamp((ROOT/"evidence/prereg-msk.txt").read_text()),"","До запуска новых JVM с решателями"],
    ]
    for name,note in (
        ("freeze-01.log","re2j: пять подходящих классов, первое условие не выполнено"),
        ("freeze-02.log","Список методов и расписание зафиксированы"),
        ("smoke-02-certify.log","Первичная проверка алиаса завершилась из-за кода помощи CLI"),
    ):
        log=ROOT/"evidence"/name
        if log.exists():
            at=datetime.fromtimestamp(log.stat().st_mtime,TZ).isoformat()
            rows.append([name,stamp(at),"",note+" · время записи лога"])
    for path in sorted((ROOT/"evidence").glob("build-*.log")):
        lines=path.read_text().splitlines()
        start=lines[0].split()[1]
        end=next((x for x in reversed(lines) if x.startswith(("OK ","FAILED "))),"")
        if end:
            rows.append([path.stem,stamp(start),stamp(end.split()[1]),
                         "Сборка прошла" if end.startswith("OK ") else "Ошибка сборки, подробности в logs"])
    for i,note in ((1,"Первая проверка. Z3 удалил файл после загрузки, логгер SortedMap завершил Java-прогоны ошибкой"),
                   (2,"Повтор. Все 6 JVM вернули код 0. Проверка алиаса после исправления валидатора")):
        p=ROOT/"runs/smoke"/f"{i:02d}"
        cases=[read(x) for x in p.glob("*/*/status.json")]
        rows.append([f"Smoke {i:02d}",stamp(min(x["start_msk"] for x in cases)),
                     stamp(max(x["end_msk"] for x in cases)),note])
    rows.append(["Сертификат smoke",stamp(read(ROOT/"runs/smoke/02/gate.json")["checked_at"]),"","3 проверки контракта и 3 живых прогона"])
    rows.append(["Основное измерение",stamp(metrics["start"]),stamp(metrics["end"]),
                 f"{f(metrics['elapsed_minutes'],1)} мин, сырые интервалы в status.json"])
    rows.append(["Независимая сверка",stamp(verified["verified_at_msk"]),"","Данные, версии, SHA-256 и повторный расчёт"])
    stock=ROOT/"runs/stock"
    if (stock/"VERIFY.json").exists():
        s=read(stock/"metrics.json")
        verify=read(stock/"VERIFY.json")
        rows.append(["Дополнительное измерение",stamp(s["start"]),stamp(s["end"]),"Штатная обработка ошибок шага"])
        rows.append(["Сверка дополнительного прогона",stamp(verify["verified_at_msk"]),"","Проверены логи JVM и потерянных состояний"])
    done=ROOT/"evidence/experiment-end-msk.txt"
    if done.exists():
        rows.append(["Закрытие эксперимента",stamp(done.read_text()),"","Сформирован комплект результатов"])
    return tab(["Событие","Начало, МСК","Конец, МСК","Исход"],sorted(rows,key=lambda x:datetime.strptime(x[1],"%d.%m.%Y %H:%M:%S.%f")))

def sections(m, v, d):
    comps=m["comparisons"]
    q=comps[D]
    relevant=any(d["comparisons"][D]["material"].values())
    recommendation = ("Сохранить штатную конфигурацию USVM. Режим cvc5 none можно проверять отдельно на целевых нагрузках с сопоставимым покрытием."
                      if relevant else "Сохранить текущий Z3 в качестве базового режима. Для cvc5 использовать вариант, который выдерживает одинаковые ограничения на конкретном целевом наборе.")
    if q["par"]["2"]["ci"][1] is not None and q["par"]["2"]["ci"][1] < 1 and not relevant:
        recommendation = "Использовать simplification=none как кандидат для дальнейшей проверки cvc5 на нагрузках этого типа. Для замены штатного Z3 требуется отдельное подтверждение."
    if (q["par"]["2"]["ci"][0] is not None and q["par"]["2"]["ci"][0] > 1):
        recommendation = "Оставить cvc5 1.4.1 с настройками по умолчанию при использовании этого бэкенда. Штатный Z3 остаётся отдельным ориентиром для выбора по покрытию и времени."
    stock_summary=""
    stock_certificate=ROOT/"runs/stock/VERIFY.json"
    if stock_certificate.exists():
        verified=read(stock_certificate)
        normal=read(ROOT/"runs/stock/metrics.json")["comparisons"][D]["par"]["2"]
        penalty=verified["par2_with_step_failure"][D]
        stock_summary=(f"<p><b>При штатной обработке ошибок шага</b> — {interval(normal)}. "
                       f"Со штрафом за потерянное состояние — {interval(penalty)}.</p>")
    intro=f"""
    <div class="hero">
    <span class="kicker">Живой USVM · Linux · 22 ноября 2026</span>
    <h1>Влияет ли <code>simplification=none</code> на время USVM?</h1>
    <p><b>cvc5 1.4.1, none / default</b> — <b>{interval(q['par']['2'])}</b> по основному штрафному времени.
       {escape(effect(q['par']['2'],relevant))}</p>
    <p><b>К штатному Z3 4.13.0.0</b> — {interval(comps['cvc5-default/z3']['par']['2'])} для cvc5 default и
       {interval(comps['cvc5-none/z3']['par']['2'])} для cvc5 none. Меньше 1 означает меньше времени.</p>
    {stock_summary}
    <p><b>Рекомендация.</b> {escape(recommendation)}</p>
    <div class="facts"><div><b>{m['methods']}</b><span>Java-методов</span></div>
    <div><b>{len(m['projects'])}</b><span>проектных групп</span></div>
    <div><b>{m['blocks']}</b><span>парных блоков</span></div>
    <div><b>{v['recorded_jvms']}</b><span>запусков JVM</span></div></div></div>
    """
    primary_rows=[]
    for name,label in PAIRS.items():
        x=comps[name]
        primary_rows.append([label,interval(x["par"]["1"]),interval(x["par"]["2"]),interval(x["par"]["3"]),
            interval(x["completed_wall"]),x["completed_wall"]["n"],
            " · ".join(f(t) for t in x["par"]["2"]["loo"])])
    timing_table=tab(["Отношение","PAR1","PAR2","PAR3","Время в паре, оба завершили",
        "Пар завершения","PAR2 без одного проекта"],primary_rows,"Отношения с 95% кластерным бутстреп-интервалом")
    counts=[]
    budgets=[]
    for a,r in m["arms"].items():
        j=r["jvm"]
        unknown = r["intended"]-r["statuses"].get("budget_complete",0)-r["statuses"].get("method_timeout",0)-r["statuses"].get("method_error",0)
        counts.append([LABEL[a],r["intended"],r["started"],r["observed"],r["statuses"].get("budget_complete",0),
            r["statuses"].get("method_timeout",0),r["statuses"].get("method_error",0),unknown,
            r["completed_unique"],f"{j['fully_completed']}/{j['planned']}",j["timeout"],j["failed"],j["verified"]])
        b=r["stop_reasons"]
        budgets.append([LABEL[a],b.get("queue_exhausted",0),b.get("coverage_goal",0),b.get("step_limit",0),
            b.get("stagnation",0),b.get("method_timeout",0), b.get("targets",0)+b.get("states_limit",0)])
    count_table=tab(["Режим","План методов","Начаты","Строки","Бюджет завершён","Таймаут метода","Ошибка метода",
                     "Прочие пропуски","Уникальных завершённых","Полных JVM","Таймауты JVM","Сбои JVM","JNI подтверждён"],
                     counts,"480 запланированных наблюдений на режим")
    budget_table=tab(["Режим","Очередь пуста","Цель охвата","Лимит шагов","Застой","Время","Другие"],
                     budgets,"Причина остановки среди выданных штатных строк")
    covrows=[]
    checkrows=[]
    jvmrows=[]
    for a,r in m["arms"].items():
        c=r["coverage_metrics"]
        sn=r["solver"]
        j=r["jvm"]
        dims=[conv(j["means"][k]) for k in ("launch_to_driver_ms","index_ms","warmup_ms","identity_ms","batch_ms","teardown_ms")]
        covrows.append([LABEL[a],f(r["coverage_pp"],2),f(r["coverage_missing_zero"],2),num(c["branch_edges"]),
            num(c["states"]),num(c["terminated"]),num(c["completed_paths"]),num(c["returned_paths"]),
            num(c["steps"]),num(c["forking_steps"])])
        checkrows.append([LABEL[a],num(sn["checks"]),num(sn["checks_assume"]),num(sn["sat"]),num(sn["unsat"]),
            num(sn["unknown"]),num(r["solver_unknown_timeout"]),num(r["solver_unknown_non_timeout"]),
            conv(sn["check_ms"]),f(sn["check_ms"]/sn["checks"],2) if sn["checks"] else "—",
            conv(sn["model_ms"]),conv(sn["create_solver_ms"]),
            f"{f(100*(r['check_share'] or 0),1)}% / {f(100*(r['model_share'] or 0),1)}%"])
        jvmrows.append([LABEL[a],conv(r["penalties"]["2"]),conv(r["method_wall_mean_ms"]),
            conv(j["external_wall_mean_ms"]),*dims,j["batches_without_startup"],f(j["peak_sample_mib"],0)])
    cpairs=[]
    for k,label in PAIRS.items():
        x=comps[k]
        cpairs.append([label,x["coverage_pp"]["n"],interval(x["coverage_pp"],2),interval(x["coverage_missing_zero_pp"],2),
            interval(x["branch_edges"]),interval(x["completed_paths"]),interval(x["states"]),
            interval(x["completion_pp"],2),interval(x["checks"]),interval(x["check_ms"])])
    phase_pairs=[[label,interval(comps[k]["jvm_external_wall"]),comps[k]["jvm_external_wall"]["n"],
                  interval(comps[k]["jvm_batch_wall"]),comps[k]["jvm_batch_wall"]["n"]] for k,label in PAIRS.items()]
    ti=timeline(m,v)
    projectrows=[]
    manifest=read(ROOT/"inputs/manifest.json")
    for p in manifest["projects"]:
        name=p["project"]
        projectrows.append([name,p["jar"]["coordinate"].rsplit("/",1)[1],p["eligible"],len({x["class"] for x in p["methods"]}),
            *[f(comps[k]["par"]["2"]["by_project"][name]["value"]) for k in PAIRS]])
    project_table=tab(["Проект","Версия","Кандидаты ASM","Классы в тесте",*PAIRS.values()],projectrows,
        "Парные отношения PAR2 внутри проекта, по 48 наблюдений на режим")
    disagreements=[]
    contexts = {"same_recorded_coverage":"Совпали все 3 метрики",
        "coverage_difference_with_time_guard":"Различие при лимите времени",
        "coverage_difference_with_solver_unknown":"Различие при UNKNOWN",
        "coverage_difference_with_other_stop":"Различие причин остановки",
        "coverage_difference_same_stop_no_unknown":"Различие при одинаковой остановке, без UNKNOWN"}
    materials={"instruction_pp":"охват инструкций","branches_rel":"рёбра ветвлений","paths_rel":"завершённые пути",
               "completion_pp":"завершение бюджета","repeated_local_change":"повторяющийся локальный разрыв"}
    for name,label in PAIRS.items():
        diag=d["comparisons"][name]
        disagreements.append([label,*[diag["context"].get(k,0) for k in contexts],
                              ", ".join(materials[k] for k,hit in diag["material"].items() if hit) or "—"])
    errors=[]
    for err in d["method_errors"]:
        errors.append([LABEL[err["arm"]],err["kind"],err["message"],err["n"],err["example"][0]])
    reasonrows=[[LABEL[a],reason,n] for a in ARMS for reason,n in m["arms"][a]["unknown_reasons"].items()]
    detailrows=[]
    for name,label in PAIRS.items():
        for x in d["comparisons"][name]["high_discordance"][:5]:
            detailrows.append([label,x["tag"],x["matches"],f(x["mean_coverage_diff"],2),x["edges_disagree"],
                x["path_count_disagree"],x["stop_disagree"]])
    stock_metrics = ROOT/"runs/stock/metrics.json"
    high_disk = max(m["max_sampled_disk_bytes"],
                    read(stock_metrics)["max_sampled_disk_bytes"] if stock_metrics.exists() else 0)
    step = read(RAW/"lock.json")
    release=step["release"]
    maps=[[item,hash] for item,hash in (("cvc5 1.4.1 ZIP",release["archive_sha256"]),
        ("cvc5 1.4.1 Java API",release["cvc5_jar_sha256"]),("libcvc5jni.so",release["cvc5_jni_sha256"]),
        ("libz3.so",release["z3_native_sha256"]["libz3.so"]),("libz3java.so",release["z3_native_sha256"]["libz3java.so"]))]
    checks=tab(["Режим","Проверки","С предположениями","SAT","UNSAT","UNKNOWN","UNKNOWN таймаут","Прочие UNKNOWN",
                "Проверки, с","На проверку, мс","Модели, с","Создание, с","Доли проверок / моделей"],checkrows)
    coverage=tab(["Режим","Инструкции, %","Пропуск как 0, %","Рёбра входа","Состояния",
                  "Терминальные события","Пути до выхода","Обычные возвраты","Шаги","Шаги с ветвлением"],covrows)
    sections = [intro,
    f"""<section id="results"><h2>Результаты</h2>{timing_table}{count_table}{budget_table}
    <p>Единица измерения — живой вызов USVM для метода. В PAR2 берётся фактическое время завершённого бюджета.
    Таймаут метода, ошибка и отсутствующая строка получают 16 с. Для проверки чувствительности использованы 8 и 24 с.
    Исключение может обрывать метод после части шагов. В сырых данных остаются накопленные счётчики.
    Условная колонка учитывает пары, в которых оба режима завершили бюджет. Строка со временем и частичным охватом при
    остановке по таймауту остаётся в охвате и получает штраф в PAR.</p>
    <p>Интервалы получены из 10 000 перевыборок целых проектов. Каждый проект входит вместе с методами,
    повторами и парными режимами. Последняя колонка показывает диапазон точечных оценок при исключении одного проекта.
    Наблюдения одного метода входят в один проектный кластер. Сопоставления с Z3 описательные,
    интервалы приведены без поправки на множественные сравнения.</p></section>""",
    f"""<section id="coverage"><h2>Охват и фактически выполненная работа</h2>{coverage}
    {tab(["Отношение","Пар охвата","Δ инструкций, п.п.","Δ с пропусками как 0, п.п.","Рёбра","Пути до выхода",
    "Состояния","Δ завершения, п.п.","Число проверок","Время проверок"],cpairs)}
    <p>Охват инструкций использует транзитивную зону USVM, которая может расширяться во время поиска.
    Рёбра — разные переходы условного оператора или switch в IR входного метода. Метрика суммируется по попыткам.
    Состояния — объекты, возвращённые штатным сборщиком COVERED_NEW. Терминальные события включают остановки состояний,
    а пути до выхода ограничены завершением входного стека. Исчерпание очереди, достижение цели покрытия и
    детерминированные лимиты считаются штатным завершением бюджета.</p>{checks}
    <p>Суммы по проверкам, извлечению и отсоединению моделей сняты внутри клиента. Разность между полным и
    solver-временем включает интерпретатор, перевод, утверждения, типовые проверки, работу с состояниями и сборку мусора.
    Суммы SAT и UNSAT относятся к запросам, реально породившимся в каждом режиме.</p>
    {tab(["Режим","Причина UNKNOWN","Число"],reasonrows) if reasonrows else "<p>В записанных проверках UNKNOWN отсутствовал.</p>"}
    </section>""",
    f"""<section id="timing"><h2>Старт JVM и накладные расходы</h2>
    {tab(["Режим","PAR2/метод, с","Время выданного метода, с","JVM целиком, с","До драйвера, с","Индексация, с",
        "Разминка, с","Проверка JNI, с","Пакет после разминки, с","Закрытие, с","Пакеты","Пик RSS, МиБ"],jvmrows)}
    {tab(["Отношение","JVM целиком","Пар JVM","Пакет после разминки","Пар пакетов"],phase_pairs)}
    <p>Причины окончания трёх разминочных методов проверены в каждой JVM.
    Таймауты разминки по режимам составили
    {", ".join(LABEL[a]+" "+str(v.get("warmup_outcomes",{}).get(a,{}).get("method_timeout",0)) for a in ARMS)}.</p>
    <p>Внешний таймер считает запуск и выход процесса, индексацию, прогрев и промежуточный сбор мусора.
    Пакет после разминки включает все выбранные методы, их создание решателя, анализ, закрытие и сбор мусора между попытками.
    Методическое время включает поиск сигнатуры и создание, анализ и закрытие JcMachine. Файлы фаз используют монотонные отметки JVM.
    Пик RSS получен периодическим опросом. Нативные файлы Z3 заранее извлечены для проверки SHA-256, поэтому внешний старт
    относится к данному способу загрузки без распаковки JAR.</p></section>""",
    f"""<section id="diagnosis"><h2>Разбор расхождений</h2>
    {tab(["Отношение",*contexts.values(),"Достигнут порог"],disagreements)}
    {tab(["Отношение","Метод","Пар","Δ инструкций, п.п.","Различаются рёбра","Различаются пути","Различается остановка"],detailrows)
       if detailrows else "<p>В парных строках совпали наблюдаемые рёбра, число путей и охват.</p>"}
    {tab(["Режим","Ошибка","Сообщение","Число","Пример"],errors) if errors else "<p>В строках методов исключения драйвера отсутствовали.</p>"}
    {primary_diagnosis(m,d)}
    <p>Расхождение при таймауте или UNKNOWN указывает, где одинаковые лимиты приводят к разному объёму
    исследованной работы. При совпавшей причине завершения возможны различия в модели, выборе состояния
    и транзитивной зоне. Эти строки фиксируют различие наблюдаемого поиска. Для вывода о противоречии SAT и UNSAT
    нужна одна и та же формула и одинаковые предположения. Smoke сравнил три конкретных распознанных результата Java с их
    фактическими исполнениями и отдельно проверил контракты SMT.</p>
    {additional_diagnosis()}
    </section>""",
    f"""<section id="design"><h2>Дизайн и ограничения</h2>
    <p>Задание перед запуском зафиксировано в PREREG.md. Список методов составлен статически по байткоду публичных JAR.
    На проект взяты четыре метода с 1–2 и четыре с 3+ условными или switch-инструкциями. Проверены проектные и классовые
    пересечения с доступными прежними манифестами. Для re2j использованы восемь методов из пяти классов.
    В статическом отборе packageurl-java имел четыре метода из трёх классов, его заменил заранее записанный резерв commons-pool2.
    Старые данные использованы для дизайна и разграничения групп.</p>
    <p>Для каждого проекта есть шесть повторов и все шесть порядков трёх режимов.
    Внутри блока методы идут в одном случайно выбранном порядке, режимы запускаются последовательно на одном ядре.
    Четыре работника закреплены за CPU 12–15, на каждом по 15 блоков, без SMT-соседей. Java 21,
    SerialGC, 2 ГиБ heap и один доступный процессор на JVM. Перед каждой серией одинаковая разминка из трёх синтетических методов.</p>
    <p>Одинаковые параметры USVM: FORK_DEPTH, seed 0, 6 с на исследование метода, 1,6 с на проверку SMT,
    1 с на операцию типов, порог 4000 шагов и 3500 шагов с последнего собранного состояния. Текущая реализация
    останавливает после превышения порога. Защита процесса — 150 с и проверка RSS 3200 МиБ после метода.
    Исключение интерпретатора считается ошибкой метода. Максимальный замеренный объём рабочего каталога в кампании
    {f(high_disk/1e6,1)} МБ при лимите 12 ГБ.</p>
    {project_table}
    <p>Набор характеризует ветвящиеся методы Java из этих библиотек при заданных лимитах и данном драйвере.
    Десять кластеров дают грубую оценку разброса. Разминка JIT, индексация библиотек, загруженность узла и
    ограничения времени влияют на прогоны. Времена инструментированного клиента относятся к этому способу запуска.
    Ночная серия 17 сравнила Z3 с JNI cvc5 1.1.2 в 211 полных парах. Серия 25б с более длинным лимитом JVM
    получила 0,989 [0,975 · 1,007] для группы настроек к default на той же старой JNI-версии.
    Серия 31 на записанных SMT-сеансах 1.4.1 с arrays-exp и лимитом 1,5 с получила 0,909 [0,704 · 0,931]
    для одного параметра. Здесь ограничения создаются заново по исполнению Java, с другим лимитом и нативным адаптером.</p></section>""",
    f"""<section id="identity"><h2>Версии и варианты</h2>
    <p>USVM commit {code('814a963354209421d36af758edbcec4295b081c4')} и KSMT core 0.5.26. Для cvc5 пересобран
    только адаптер из тега 0.5.26, с shim для доступа к указателям и обработкой новых значений Kind.
    Оба режима cvc5 используют статический JNI из одного официального Linux x86-64 релиза и один Java API.
    Z3 4.13.0.0 работает через штатный JNI KSMT на тех же байтах из JAR. Все три режима выполнялись в процессе JVM.</p>
    <p>Фабрика проверяла версию и значение упрощения, а после первой реальной проверки JVM сохраняла параметры
    и SHA-256 библиотек из /proc/self/maps. У cvc5 по умолчанию действует <code>simplification=batch</code>.
    Режим none устанавливает <code>simplification=none</code>. Вторая изменившаяся строка
    <code>simplification-mode</code> — алиас этой настройки. Общее состояние cvc5 после первой проверки включает
    <code>incremental=true</code>, <code>produce-models=true</code>, <code>produce-unsat-cores=true</code>,
    <code>fp-exp=true</code>, <code>arrays-exp=false</code>, <code>seed=0</code> и <code>tlimit-per=1600</code>.
    Для Z3 значение random_seed=0 зафиксировано в справке созданного нативного солвера. Таймаут передаётся из
    одинаковых UMachineOptions штатному адаптеру. Запрос от клиента на Array/BV/UF/FP оставляет
    штатный общий солвер Z3, поскольку набор отсутствует в списке оптимизированных комбинаций KSMT 0.5.26.</p>
    {tab(["Объект","SHA-256"],maps)}
    <p>Пройдены SAT и UNSAT, битвекторная, массивная и FP-модель, detach после закрытия, push/pop, предположения и ядра.
    В живом Java smoke получены три вычисляемых результата −11, 22 и 33. Для каждого выполнен вход-свидетель на JVM.
    Недостижимый возврат 999 отсутствует. <a href="https://github.com/cvc5/cvc5/releases/tag/cvc5-1.4.1">Релиз cvc5 1.4.1</a>.</p>
    </section>""",
    f"""<section id="timeline"><h2>Хронология и верификация</h2>{ti}
    <p>При первом статическом отборе re2j исчерпал классы. Исправление правил зафиксировано до новых исходов.
    Первая сборка адаптера выявила новые перечисления библиотеки, следующая — положение ветки else.
    Компиляция клиентских классов потребовала корректного списка Kotlin friend paths и явного приведения интерфейса KSolver.
    Два автоматических подтверждения smoke были исправлены по сохранённым логам: валидатор принял алиас параметра
    и код помощи CLI 1. Сравнение началось после подтверждения всех условий.</p>
    <p>Независимый скрипт проверил {v['recorded_jvms']} интервалов JVM, {v['method_attempts']} запланированных попыток,
    порядок на ядрах, согласованность счётчиков, загруженные файлы и полный вектор cvc5.
    Отдельным расчётом воспроизведены оценки PAR, охвата и проектные интервалы. Задокументировано
    {num(len(v['raw_file_sha256']))} SHA-256 сырых файлов.</p></section>""",
    f"""<section id="files"><h2>Файлы на butvinm-work</h2><p>{code(ROOT)}</p>
    {tab(["Артефакт","Относительный путь"],[
        ("План и поправки","PREREG.md · PREREG-amendment-01.md · PREREG-amendment-02.md"),
        ("Методы, JAR и проектный разрез","inputs/manifest.json"),
        ("Порядок и загрузка ядер","inputs/schedule.json"),
        ("Данные, отметки времени и код возврата","runs/primary/<проект>-r<повтор>/<режим>/"),
        ("Сводка всех JVM","runs/primary/supervisor.jsonl"),
        ("Версии, карты памяти и настройки","runs/primary/<блок>/<режим>/backend.json · backend-maps.txt"),
        ("Smoke и ошибки настройки","runs/smoke/01/ · runs/smoke/02/ · evidence/ · engineer.log"),
        ("Оценки, полный список наблюдений и диагностика","runs/primary/metrics.json · method-outcomes.tsv · diagnostics.json"),
        ("Независимая проверка и хеши","runs/primary/VERIFY.json"),
        ("Исходный код, скрипты и запуск","checkout/ · scripts/ · REPRODUCE.md"),
        ("Статус","current.json")])}</section>"""
    ]
    return "\n".join(sections)

def primary_diagnosis(m,d):
    pairs=m["comparisons"]
    q=pairs[D]
    causes=d["comparisons"][D]["context"]
    def by_method(comp, class_method):
        return next(row for row in pairs[comp]["discordance"]["methods"] if class_method in row["tag"])
    def spread(values):
        return f"{min(values)}–{max(values)}"
    regex = by_method("cvc5-default/z3","Regexp.equals")
    matcher = by_method(D,"Matcher.start")
    left = spread([r["completed_paths"][0] for r in regex["rows"]])
    right = spread([r["completed_paths"][1] for r in regex["rows"]])
    left_checks = spread([r["check"][0] for r in regex["rows"]])
    right_checks = spread([r["check"][1] for r in regex["rows"]])
    cap = causes.get("coverage_difference_with_time_guard",0)
    discord = q["coverage_pp"]["n"]-causes.get("same_recorded_coverage",0)
    z3d = pairs["cvc5-default/z3"]["completed_paths"]["value"]
    z3n = pairs["cvc5-none/z3"]["completed_paths"]["value"]
    return f"""
    <p>В контрасте cvc5 none/default из {discord} пар с разными наблюдаемыми метриками {cap}
    имели хотя бы одну остановку по времени. В <code>Matcher.start</code> число завершённых путей
    отличалось в {matcher['path_count_disagree']} из {matcher['matches']} повторов, при тех же рёбрах входного метода.
    Причины остановки в этих повторах включают застой и лимиты шагов или времени. Средний охват инструкций
    в контрасте изменился на {f(q['coverage_pp']['value'],2)} п.п., но локальные различия сохраняются.
    В общих выданных парах число проверок изменилось до {f(q['checks']['value'])}×, время проверок
    до {f(q['check_ms']['value'])}×, а время пакета после разминки до {interval(q['jvm_batch_wall'])}.</p>
    <p>В сравнении с Z3 отношения числа завершённых путей для cvc5 default и none составили {f(z3d)} и {f(z3n)}.
    В <code>Regexp.equals</code> за шесть повторов cvc5 default выдал {left} путей и {left_checks} SMT-проверок,
    а Z3 — {right} путей и {right_checks} проверок. В этих парах обе стороны достигли лимита метода.
    Это различие в исследованной работе при общем бюджете.</p>
    """

def additional_diagnosis():
    probe = ROOT / "runs/stock"
    if not (probe/"decision.json").exists():
        return ""
    decision = read(probe/"decision.json")
    shared = decision["all_three_error_method_blocks"]
    if not decision["run"]:
        return (f"<p>Во всех трёх режимах одновременно завершились ошибкой {shared} из 480 "
                "парных наблюдений. Условие дополнительного прогона по DIAGNOSTIC-PROTOCOL.md не выполнено.</p>")
    runs = read(probe/"metrics.json")
    verified = read(probe/"VERIFY.json")
    assert verified["ok"] and verified["metrics_sha256"] == sha(probe/"metrics.json")
    prep = []
    errors = []
    for arm in ARMS:
        s = runs["arms"][arm]
        st = verified["observations"][arm]
        err = verified["method_step_failures"][arm]
        prep.append([LABEL[arm],s["intended"],s["statuses"].get("budget_complete",0),
                     s["statuses"].get("method_timeout",0),s["statuses"].get("method_error",0),
                     st.get("primary_same_cells_error",0),st.get("stock_returned_after_strict_error",0),
                     st.get("started_with_step_failure",0),st.get("budget_complete_with_step_failure",0),
                     sum(err.values()),f(s["coverage_pp"],2),s["jvm"]["verified"]])
        for k,n in sorted(err.items(),key=lambda z:-z[1]):
            errors.append([LABEL[arm],k,n,verified["first_examples"].get(k,"")])
    times = [[label,interval(runs["comparisons"][k]["par"]["2"]),
              interval(verified["par2_with_step_failure"][k]),
              interval(runs["comparisons"][k]["coverage_pp"],2)] for k,label in PAIRS.items()]
    return f"""
    <h3>Дополнительный живой прогон со штатной реакцией на исключения</h3>
    <p>В основной серии {shared} из 480 наблюдений завершились ошибкой одновременно во всех трёх режимах.
    Стек общего сбоя ведёт в StepScope.doWithState через JcMethodApproximationResolver. При штатной политике
    USVM записывает ошибку шага, удаляет это состояние и продолжает поиск. В обеих ветках cvc5 отдельно
    встречается неподдерживаемый перевод fp.to_ieee_bv в KCvc5ExprInternalizer. Логи позволяют различить
    сбой общего интерпретатора и отказ адаптера.</p>
    <p>Другой слой запуска меняет только реакцию на исключение и добавляет идентификаторы методов в лог.
    В 30 дополнительных блоках те же проекты и методы, по три сбалансированных порядка на проект.
    Основная серия сохранена целиком. Измерение длилось с {stamp(runs["start"])} до {stamp(runs["end"])} МСК.
    Проверка сырых данных завершилась {stamp(verified["verified_at_msk"])} МСК.</p>
    {tab(["Режим","План","Бюджет завершён","Время","Ошибка метода","Ошибок в тех же ячейках строгой серии",
          "Возвращены после строгой ошибки","Вызовов со сбоем шага","Завершили со сбоем шага",
          "Сбоев шага","Охват инструкций, %","Подтверждённых JVM"],prep)}
    {tab(["Отношение","Обычный PAR2","PAR2 со штрафом за сбой шага","Δ инструкций, п.п."],times)}
    <p>При штатной политике оба режима cvc5 сохранили меньшие счётчики путей до выхода относительно Z3.
    Отношения составили {f(runs['comparisons']['cvc5-default/z3']['completed_paths']['value'])}
    для default и {f(runs['comparisons']['cvc5-none/z3']['completed_paths']['value'])} для none.
    В дополнительной колонке любое исключение шага получает штраф 16 с, даже при завершившемся вызове.
    Разность между двумя оценками показывает чувствительность к способу учёта потерянных состояний.
    Временные отношения рассчитаны между решателями внутри дополнительной серии.
    Те же методы дают проверку устойчивости результата к обработке ошибок.</p>
    {tab(["Режим","Класс исключения шага","Число","Пример сообщения"],errors) if errors else ""}
    <p>Протокол, сырые отметки, логи и сертификат на butvinm-work находятся в
    <code>DIAGNOSTIC-PROTOCOL.md</code> и <code>runs/stock/</code>.</p>
    """

STYLE = """
:root{color-scheme:light;--ink:#122639;--muted:#526579;--faint:#eef3f7;--line:#d9e2e9;--accent:#126c69}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:#f4f7fa;font:16px/1.55 system-ui,sans-serif}
main{max-width:1360px;margin:auto;padding:30px 36px 70px}nav{display:flex;gap:13px;flex-wrap:wrap;margin-bottom:18px}
a{color:#055f5e;text-decoration-thickness:1px;text-underline-offset:3px}
.hero,section{background:white;border:1px solid var(--line);border-radius:14px;margin:16px 0;padding:27px 32px}
.hero{border-top:6px solid var(--accent)}.kicker{color:#196b68;font-size:.8rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
h1{font-size:clamp(2rem,4.4vw,3.4rem);font-weight:750;letter-spacing:-.04em;line-height:1.1;margin:.45em 0}
h2{font-size:1.55rem;line-height:1.25;margin:0 0 12px}p{margin:10px 0 18px;max-width:107ch}
.facts{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:20px}
.facts div{background:var(--faint);border-radius:8px;padding:7px 14px}.facts b{display:block;font-size:1.5rem}.facts span{color:var(--muted)}
.table{overflow:auto;margin:13px 0 20px}table{width:100%;border-collapse:collapse;font-size:.85rem;line-height:1.34}
td,th{padding:8px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
th{background:#eaf0f4}td:last-child{overflow-wrap:anywhere}tbody tr:nth-child(even){background:#f7f9fa}
caption{text-align:left;color:var(--muted);padding-bottom:6px}code{font-size:.9em;overflow-wrap:anywhere}nav a{font-size:.86rem}
@media(max-width:720px){main{padding:15px}.hero,section{padding:17px}.facts{grid-template-columns:repeat(2,1fr)}}
@media print{body{background:white}main{padding:0}.hero,section{border-radius:0}nav{display:none}.table{overflow:visible}}
"""

def main():
    m,v,d=[read(RAW/f"{p}.json") for p in ("metrics","VERIFY","diagnostics")]
    assert v["ok"] and v["metrics_sha256"] == sha(RAW/"metrics.json")
    nav="".join(f'<a href="#{k}">{label}</a>' for k,label in [
        ("results","Результаты"),("coverage","Охват"),("timing","Время JVM"),("diagnosis","Расхождения"),
        ("design","Дизайн"),("identity","Версии"),("timeline","Хронология"),("files","Файлы")])
    page=f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Живой USVM · cvc5 1.4.1 и Z3</title>
<style>{STYLE}</style></head>
<body><main><nav aria-label="Содержание">{nav}</nav>
{sections(m,v,d)}
</main></body></html>
"""
    tmp = ROOT/"report.tmp"
    tmp.write_text(page)
    tmp.replace(ROOT/"report.html")
    print("Report", ROOT/"report.html", sha(ROOT/"report.html"))

if __name__ == "__main__":
    main()
