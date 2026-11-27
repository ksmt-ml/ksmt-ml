"""Render the Russian report and short result from the independent audit."""
from collections import Counter, defaultdict
from html import escape
from verify_live import ROOT, LIVE, date, read, sha, stamp

ARMS=("default","smac","sol","z3")
NAMES={"default":"cvc5 default","smac":"SMAC","sol":"Sol","z3":"Z3"}
HIST_NAMES={"cvc5-default":"cvc5 default","cvc5-none":"cvc5 none","z3":"Z3"}

def d(x,n=2):
    return f"{x:.{n}f}".replace(".",",") if x is not None else "н/д"

def signed(x):
    return ("+" if x>0 else "")+d(x) if x is not None else "н/д"

def interval(info,n=3,sign=False):
    f=signed if sign else lambda x:d(x,n)
    return f'{f(info["value"])} [{f(info["ci"][0])}; {f(info["ci"][1])}]'

def opts(option):
    return ", ".join(f"{k}={v}" for k,v in option.items()) or "default"

def codes(txt):
    return f"<code>{escape(str(txt))}</code>"

def optcell(option):
    return codes(opts(option))

def table(headers,rows):
    head="".join(f"<th scope='col'>{escape(h)}</th>" for h in headers)
    body=[]
    for row in rows:
        a,*b=row
        body.append(f"<tr><th scope='row'>{a}</th>"+"".join(f"<td>{x}</td>" for x in b)+"</tr>")
    return f"<div class='scroller'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"

def text(value):
    return escape(str(value))

def join_counts(counts):
    return ", ".join(f"{k} × {v}" for k,v in sorted(counts.items(),key=lambda r:(-r[1],r[0]))) or "0"

def rationale(r):
    labels={"paths":"пути","edges":"рёбра","steps":"шаги","coverage_pp":"покрытие инструкций",
            "failed":"штрафные вызовы","jvm_failed":"сбои JVM","step_errors":"ошибки шага",
            "solver_unknown":"UNKNOWN","solver_timeouts":"таймауты решателя","incomplete":"пропуск или отсутствие сверки"}
    reasons=[]
    for key in r["violations"]:
        if key.startswith("paths:"):
            reasons.append("пути в "+key.split(":",1)[1])
        else:
            reasons.append(labels.get(key,key))
    return "да" if r["admissible"] else text(", ".join(reasons) or "порог скорости")

def duration(start,end):
    return (date(end)-date(start)).total_seconds()

def span(parent):
    stamps=[read(f) for f in parent.glob("**/status.json")]
    return (min(r["start_msk"] for r in stamps),max(r["end_msk"] for r in stamps)) if stamps else ("н/д","н/д")

def historical():
    h=read(ROOT/"evidence/historical.json")
    details=read(ROOT/"evidence/historical-details.json")
    strict,stock=h["primary"],h["stock"]
    old_pairs=[]
    breakdown=[]
    condition=[]
    for name,panel in (("Строгая",strict),("Штатная",stock)):
        for a,c in panel["comparisons"].items():
            n,x,y=c["counts"]
            old_pairs.append((name,HIST_NAMES[a],n,x,y,d(100*(x/y-1))+"%",panel["intended_blocks"]))
            q=c["context"]
            condition.append((name,HIST_NAMES[a],q["all_discordant"],q["exploration_timeout"],q["unknown"],q["step_drop"]))
        for a,r in panel["arms"].items():
            reasons=r["unknown_reasons"]
            unknown_t=reasons.get("TIMEOUT",0)+reasons.get("canceled",0)
            breakdown.append((name,HIST_NAMES[a],r["returned"],sum(r["errors"].values()),r["affected"],
                sum(r["drops"].values()),r["stops"].get("method_timeout",0),unknown_t,
                sum(reasons.values())-unknown_t,r["checks"],text(join_counts({k:v for k,v in r["stops"].items() if k!="method_timeout"}))))
    projects=[]
    for p,r in strict["comparisons"]["cvc5-default"]["by_project"].items():
        n,x,y=r
        sn,sx,sy=stock["comparisons"]["cvc5-default"]["by_project"][p]
        projects.append((codes(p),n,f"{x} / {y}",d(100*(x/y-1))+"%" if y else "н/д",sn,f"{sx} / {sy}",
                         d(100*(sx/sy-1))+"%" if sy else "н/д"))
    all_methods=[]
    for series, dct in details.items():
        for label,k in (("Устойчиво чистые","consistently_clean"),("Повторяемая ошибка метода","all_arms_method_error"),
                        ("Повторяемый отброс состояний","all_arms_step_affected"),("Повторяемый лимит исследования","all_arms_exploration_timeout")):
            examples=dct[k]
            all_methods.append(("Строгая" if series=="primary" else "Штатная",label,len(examples),
                                "<br>".join(codes(x) for x in examples[:4]) or "—"))
    biggest=[]
    for tag,gap in strict["comparisons"]["cvc5-default"]["worst"][:5]+strict["comparisons"]["cvc5-default"]["best"][:2]:
        biggest.append((codes(tag),f"{gap:+}"))
    return f"""
<h2 id="history">Диагностика перед поиском</h2>
<p>Исходные 14–16% — относительная разница <strong>суммарного числа завершённых путей</strong>
между cvc5 и Z3 на общих парах метод/повтор, где оба вернули строку со статусом OK. В числитель и знаменатель входят
также результаты после лимита исследования. Успешный и исключительный выходы из анализируемой программы могут
завершать путь. Одна и та же сигнатура учитывается заново в каждом повторе. В обоих строгих контрастах
знаменатель совпал: 7 753 пути Z3 из 367 пар из 480 плановых. Всего 100/480 наблюдений закончилось исключением во всех
движках, при этом только 299/480 наблюдений завершились без лимита исследования и без фатальной ошибки одновременно
во всех трёх. Строгая серия включала 80 методов × 6 повторов (10 проектов). Штатная диагностика использовала те же
методы × 3 повтора. Интервалы исходных серий в МСК: строгая
<time>{strict["interval"][0]}</time> — <time>{strict["interval"][1]}</time>, штатная
<time>{stock["interval"][0]}</time> — <time>{stock["interval"][1]}</time>. Их данные используются только здесь.</p>
{table(("Политика","cvc5","Пар","Путей cvc5","Путей Z3","Δ к Z3","Плановых пар"),old_pairs)}
<p>Пути по проектам. В строгой серии hppc дал крупнейший абсолютный вклад в дефицит default (−367), RoaringBitmap дал
−277, re2j дал −216, lucene-core дал −182. В automaton наблюдалось +52. В штатной серии на некоторых методах
суммарные пути выросли после продолжения исследования с оставшимися состояниями. Строки между политиками имеют разные
парные знаменатели.</p>
{table(("Проект","Строгие пары","cvc5 / Z3","Δ","Штатные пары","cvc5 / Z3","Δ"),projects)}
<p>Таймаут запроса решателя, лимит исследования метода и исключение шага — раздельные события.
У cvc5 с настройками по умолчанию было 2 строгих solver timeout на {strict["arms"]["cvc5-default"]["checks"]}
check и 70 остановок исследования; в {strict["comparisons"]["cvc5-default"]["context"]["exploration_timeout"]}
из {strict["comparisons"]["cvc5-default"]["context"]["all_discordant"]} пар с разным числом путей сработал лимит
исследования хотя бы у одного движка. Совпадение событий само по себе причинность не устанавливает.
В штатной серии cvc5 также возвращал UNKNOWN на константном массиве STORE_ALL (30 default, 26 none).
Для Z3 причиной UNKNOWN с лимитом отмечен <code>canceled</code> по инструментированному адаптеру.</p>
{table(("Политика","Движок","Вернул","Ошибки метода","Методов с отбросом","Отбросов","Лимит исследования",
        "Solver timeout","Другие UNKNOWN","Check","Другие остановки"),breakdown)}
{table(("Политика","Режим","Разный счётчик путей","С лимитом исследования","С UNKNOWN","С отбросом"),condition)}
<p>Чаще всего строгий USVM останавливался на guard шага <code>Caller should check before processing the current hop further</code>
(по 84 у каждого движка). Также наблюдались приведение <code>KIteExpr</code> к <code>UConcreteHeapRef</code>,
<code>nullRef</code>, отсутствующий тип cglib и ограничение адаптера cvc5 на <code>fp.to_ieee_bv</code>.
В штатной серии это проявлялось как продолжение после отброшенных состояний. Обе политики выделяют общие проблемные
функции. Обычные исключительные возвраты самой Java-программы хранятся отдельно.</p>
<details><summary>Повторяемость по методам и вклад отдельных методов</summary>
{table(("Политика","Исход во всех движках и повторах","Методов","Примеры"),all_methods)}
{table(("Метод, строгая серия","Путей cvc5 default минус Z3"),biggest)}
<p>Для старой строгой серии PAR2 <code>none / default</code> был 0,985 [0,959; 1,005],
для штатной серии с учётом ошибок шагов 0,998 [0,980; 1,016].</p>
<p><a href="evidence/history-primary-methods.tsv">Строгая серия по методам</a> ·
<a href="evidence/history-stock-methods.tsv">Штатная серия по методам</a> ·
<a href="evidence/historical.json">Сводка и знаменатели</a> ·
<a href="../live-client-141/report.html">Архив предыдущего эксперимента</a></p>
</details>"""

def report_results(v):
    test=v["test"]
    contrasts=v["contrasts"]
    lock=read(LIVE/"test-lock.json")
    gate=v["gates"]
    s_rows=[]
    for a,x in test.items():
        s_rows.append((NAMES[a],optcell(lock["configs"][a]),x["n"],f'{x["verified"]}/{x["jvms"]}',
            d(x["par2_ms"]/x["n"]/1e3),d(x["wall_ms"]/x["wall_ms_n"]/1e3) if x["wall_ms_n"] else "н/д",
            d(x["batch_ms"]/x["batch_ms_n"]/1e3) if x["batch_ms_n"] else "н/д",
            x["paths"],x["returned_paths"],x["edges"],d(x["coverage"]/x["n"]),x["steps"],x["states"]))
    quality=[]
    for a,x in test.items():
        quality.append((NAMES[a],x["failed"],x["exploration_timeouts"],x["method_errors"],x["jvm_failed"],
            x["missing"],x["unverified"],x["step_errors"],x["solver_timeouts"],x["other_unknown"],
            x["checks"],d(x["check_ms"]/1000),text(join_counts(x["stops"]))))
    ratios=[]
    for a,r in contrasts.items():
        ratios.append((NAMES[a],*[interval(r[k]) for k in ("par2_ms","wall_ms","paths","edges","steps")],
                       interval(r["coverage"],sign=True),"да" if gate[a]["feasible"] else "нет"))
    groups=[]
    for p,x in v["test_by_project"].items():
        baseline=x["default"]
        for a in ARMS:
            q=x[a]
            groups.append((codes(p),NAMES[a],q["n"],q["paths"],q["edges"],d(q["coverage"]/q["n"]),q["steps"],
                           q["failed"],q["exploration_timeouts"],q["step_errors"],q["solver_unknown"],
                           d(q["par2_ms"]/baseline["par2_ms"],3)))
    loo=[]
    for a,r in contrasts.items():
        for p,q in r["par2_ms"]["leave_one_out"].items():
            loo.append((NAMES[a],codes(p),d(q,3),d(r["paths"]["leave_one_out"][p],3)))
    breakdown=[]
    for a,x in test.items():
        breakdown.append((NAMES[a],text(join_counts(x["step_error_types"])),text(join_counts(x["unknown_reasons"])),
                         text(join_counts(dict(Counter(x["terminal_exceptions"]).most_common(6))))))
    by_tag=defaultdict(lambda:defaultdict(Counter))
    test_rows=read(ROOT/"evidence/test-by-method.json")
    for row in test_rows:
        t=by_tag[row["tag"]][row["arm"]]
        t.update({k:row[k] for k in ("paths","edges","failed","checks","step_errors","exploration_timeouts","solver_unknown")})
    methods=[]
    for tag,arms in sorted(by_tag.items()):
        methods.append((codes(tag),*(f'{arms[a]["paths"]} / {arms[a]["edges"]} / {arms[a]["failed"]}' for a in ARMS)))
    context=[]
    pairs={(r["tag"],r["block"],r["arm"]):r for r in test_rows}
    for a in ("smac","sol","z3"):
        c=Counter()
        for r in test_rows:
            if r["arm"]!=a:
                continue
            b=pairs[r["tag"],r["block"],"default"]
            if r["paths"]!=b["paths"]:
                c["path"]+=1
                c["timed"]+=bool(r["exploration_timeouts"] or b["exploration_timeouts"])
                c["drops"]+=bool(r["step_errors"] or b["step_errors"])
                c["unknown"]+=bool(r["solver_unknown"] or b["solver_unknown"])
            c["edges"]+=r["edges"]!=b["edges"]
            c["steps"]+=r["steps"]!=b["steps"]
            c["stops"]+=r["stops"]!=b["stops"]
        context.append((NAMES[a],c["path"],c["timed"],c["drops"],c["unknown"],c["edges"],c["steps"],c["stops"]))
    return f"""
<h2 id="test">Закрытый живой тест</h2>
<p>Каждый движок: 4 проекта × 8 методов × 4 повтора, 128 вызовов и 16 JVM. Все контрасты ниже с cvc5 default.
PAR2 в секундах на запланированный вызов. Полный wall включает индексацию, три прогрева и завершение процесса;
время пакета охватывает все вызовы методов данной JVM после прогрева. Инструкции — среднее процентов на
вызов, с нулём для пропуска. Число путей, рёбер и шагов суммируется по вызовам.</p>
{table(("Режим","Опции","Вызовы","JVM сверено","PAR2, с","Полный JVM, с","Пакет, с","Пути","Успешные выходы",
        "Рёбра","Инструкции, %","Шаги","Состояния"),s_rows)}
{table(("Режим","PAR2-события","Лимит исследования","Ошибка метода","Сбой JVM","Пропуски","Нет сверки",
        "Ошибки шага","Solver timeout","Прочие UNKNOWN","Check","Check, с","Исходы"),quality)}
<p>Расчёт 95% интервалов по 10 000 повторным выборкам четырёх целых проектов. Для времени отношение меньше 1
означает сокращение. Для покрытия инструкций показана разность в процентных пунктах.
Допустимость оценивает пороги покрытия и надёжности на точечных парных данных. Убедительность требует также
2% снижения PAR2, верхней границы ДИ PAR2 ниже 1 и нижней границы ДИ путей не ниже 0,98.</p>
{table(("Режим / default","PAR2 [95% ДИ]","Полный wall [95% ДИ]","Пути [95% ДИ]","Рёбра [95% ДИ]",
        "Шаги [95% ДИ]","Инструкции, Δ п.п.","Допустим"),ratios)}
<p>Расхождения объёма исследования из 128 пар на контраст. Столбцы причин для пар с разным числом путей
могут пересекаться. Разные шаги и check показывают, насколько результат времени зависит от работы USVM на
этих запусках. Совпадение счётчиков также не доказывает тождественность путей.</p>
{table(("Режим / default","Разные пути","С лимитом исследования","С отбросом","С UNKNOWN","Разные рёбра",
        "Разные шаги","Разная остановка"),context)}
<details><summary>Проекты, ошибки и чувствительность к одному проекту</summary>
{table(("Проект","Движок","Вызовы","Пути","Рёбра","Инструкции, %","Шаги","PAR2-события","Лимит",
        "Ошибки шага","UNKNOWN","PAR2 / default"),groups)}
{table(("Режим / default","Без проекта","PAR2","Пути"),loo)}
{table(("Режим","Исключения шага","Причины UNKNOWN","Частые исключительные выходы программы"),breakdown)}
</details>
<details><summary>Все 32 метода теста</summary>
<p>В ячейке показаны пути / рёбра / вызовы со штрафом, сумма четырёх повторов.</p>
{table(("Метод","cvc5 default","SMAC","Sol","Z3"),methods)}
</details>
"""

def searches(v):
    arm_table=[]
    train_rows=[]
    validation_rows=[]
    baseline_rows=[]
    proposal_rows=[]
    timed=[]
    fastest=[]
    evaluated={}
    smoke=read(ROOT/"inputs/space.json")
    timed.append(("Контрактный и живой smoke",smoke["start_msk"],smoke["end_msk"]))
    timed.append(("Проверка сессии Sol",*span(ROOT/"raw/sol-preflight")))
    timed.append(("Сборка overlay, попытка 1",*span(ROOT/"raw/build")))
    timed.append(("Сборка overlay, попытка 2",*span(ROOT/"raw/build-02")))
    for a in ("smac","sol"):
        data=v["arms"][a]
        c=data["choice"]
        arm_dir=LIVE/a
        result=read(arm_dir/"training.json") if (arm_dir/"training.json").exists() else []
        novel=sum(t["mask"] not in (0,8) for t in result)
        evaluated[a]={t["mask"] for t in result}
        if result:
            best=min(result,key=lambda t:t["result"]["par2_ratio"])
            r=best["result"]
            fastest.append(f'<p>{NAMES[a]}, самый низкий train PAR2 среди кандидатов: '
                           f'{optcell(smoke["combinations"][str(best["mask"])]["options"])}. '
                           f'PAR2 {d(r["par2_ratio"],3)} от default, пути {d(r["paths_ratio"],3)}. '
                           f'Допуск к выбору: {rationale(r)}.</p>')
        arm_table.append((NAMES[a],c["proposals"],c["new_trials"],c["complete_trials"],novel,c["feasible_train"],
            c["admissible_train"],len(c["validation"]),c["feasible_validation"],c["admissible_validation"],
            optcell(c["options"]),d(c["wall_s"]),c["max_wall_s"]))
        timed.append((f"{NAMES[a]}, всё плечо",c["start_msk"],c["end_msk"]))
        timed.append((f"{NAMES[a]}, train default",*span(arm_dir/"train-default")))
        if c["validation_baseline"]:
            timed.append((f"{NAMES[a]}, validation",*span(arm_dir/"validation")))
        for phase in ("train","validation"):
            anchor=data["training_baseline" if phase=="train" else "validation_baseline"]
            if anchor:
                baseline_rows.append((NAMES[a],phase,anchor["n"],anchor["paths"],anchor["edges"],anchor["failed"],
                    anchor["step_errors"],anchor["solver_unknown"],d(anchor["par2_ms"]/anchor["n"]/1000),
                    d(anchor["wall_ms"]/anchor["wall_ms_n"]/1000) if anchor["wall_ms_n"] else "н/д"))
        for t in result:
            r=t["result"]
            space=smoke["combinations"][str(t["mask"])]["options"]
            times=span(arm_dir/f"train-{t['mask']:02d}")
            train_rows.append((NAMES[a],optcell(space),*times,d(r["par2_ratio"],3),d(r["wall_ratio"],3),
                d(r["paths_ratio"],3),d(r["edges_ratio"],3),d(r["steps_ratio"],3),signed(r["coverage_delta_pp"]),
                r["totals"]["failed"],r["totals"]["step_errors"],r["totals"]["solver_unknown"],
                rationale(r)))
        for t in c["validation"]:
            r=t["result"]
            idx=c["advanced"].index(t["mask"])+1
            times=span_for_arm(arm_dir/"validation",f"candidate{idx}")
            validation_rows.append((NAMES[a],optcell(smoke["combinations"][str(t["mask"])]["options"]),*times,
                    d(r["par2_ratio"],3),d(r["wall_ratio"],3),d(r["paths_ratio"],3),
                    rationale(r)))
        if a=="sol":
            for call in data["inference"]:
                folder=arm_dir/"model"/call["call"]
                p=read(folder/"proposal.json")
                mask=call["mask"]
                proposal_rows.append((call["call"],call["start"],call["end"],call["train_trials_seen"],"да" if call["inline_train"] else "нет",
                    "да" if call["completed"] else "нет",
                    optcell(smoke["combinations"][str(mask)]["options"]) if mask is not None else "н/д",
                    text(p["reason"]) if mask is None else f"<span lang='en'>{text(p['reason'])}</span>"))
    timed.append(("Закрытый тест",*span(LIVE/"test")))
    for a in ARMS:
        timed.append((f"Тест, {NAMES[a]}",*span_for_arm(LIVE/"test",a)))
    timed.append(("Сверка",v["start_msk"],v["end_msk"]))
    return f"""
<h2 id="search">Поиск и заморозка</h2>
<p>SMAC 2.4.0 оценивал варианты через Optimizer ask/tell и получал фактически измеренный объектив.
Sol работал через существующий вход Codex ChatGPT. Изоляция предоставляла только новый обезличенный
train и пространство опций, отдельный рабочий каталог для каждого запроса, параметры механизма отключали
сеть для оболочки и поиск. Оптимизаторы не видели результаты validation и теста. Для сравнения «новых» здесь
использованы два ранее испытанных живых набора cvc5: default и <code>simplification=none</code>.
Более ранние replay-кандидаты в эту проверку новизны не входят.</p>
{table(("Плечо","Предложения","Измерено","Полных","Новых к live","Допустимых train","С выигрышем train",
        "На validation","Допустимых validation","С выигрышем validation","Заморожено","Факт, с","Бюджет, с"),arm_table)}
{''.join(fastest)}
<p>Оба плеча измерили {len(evaluated["smac"] & evaluated["sol"])} общих наборов сверх default.
«Допустимых» проверяет покрытие и надёжность. «С выигрышем» добавляет PAR2 ≤0,98 и полный JVM wall ≤1,02.
SMAC мог повторно запросить уже измеренный набор, такой запрос получал сохранённый результат. Sol получал
свои прежние значения перед каждым новым предложением. Для каждого плеча baseline измерялся внутри его окна.
Время на подготовку, вывод модели и проверку входит в окно поискового плеча. Незаполненный остаток окна
ограничен резервом validation и условием запуска только полного пакета.</p>
<details><summary>Опорные измерения default в поисковых окнах</summary>
{table(("Плечо","Этап","Вызовы","Пути","Рёбра","Штрафы","Ошибки шага","UNKNOWN","PAR2, с","Полный JVM, с"),baseline_rows)}
</details>
<details open><summary>Измеренные конфигурации train</summary>
{table(("Плечо","Опции","Старт МСК","Конец МСК","PAR2","Полный wall","Пути","Рёбра","Шаги",
        "Инструкции, Δ п.п.","PAR2-события","Отбросы","UNKNOWN","Прошёл отбор / причины"),train_rows)}
</details>
<details open><summary>Кандидаты на validation</summary>
{table(("Плечо","Опции","Первый JVM МСК","Последний JVM МСК","PAR2","Полный wall","Пути",
        "Прошёл отбор / причины"),validation_rows) if validation_rows else "<p>Ни один train-кандидат не выполнил условия передачи на validation. Валидационный default измерен в рамках каждого плеча согласно записанным назначениям.</p>"}
</details>
<details><summary>Запросы Sol и наблюдения на момент запроса</summary>
<p>Столбец «Измерений видно» не включает базовый пакет. Первый запрос дал
предложение без прочитанного train, поскольку инструмент чтения не запустился с лимитом виртуальной памяти.
Со второго запроса тот же обезличенный снимок передавался непосредственно через stdin внутри прежней изоляции. Ответы приведены на языке модели.
Сырые события каждой сессии и точный доступный снимок сохранены отдельно.</p>
{table(("Запрос","Старт МСК","Конец МСК","Измерений видно","Train во входе","Ответ получен","Опции","Причина"),proposal_rows)}
</details>
<details><summary>Фактические интервалы фаз</summary>
{table(("Фаза","Старт МСК","Конец МСК"),timed)}
<p>Время каждого процесса, коды возврата и сработавшие ограничения сохранены в статусах JVM и запросов.
Заморозка перед тестом: <time>{v["lock"]}</time>.</p>
</details>
"""

def span_for_arm(where,arm):
    ss=[read(f) for f in where.glob(f"*/{arm}/status.json")]
    return (min(s["start_msk"] for s in ss),max(s["end_msk"] for s in ss)) if ss else ("н/д","н/д")

def conclusion(v):
    good=[]
    for a in ("smac","sol"):
        cfg=v["arms"][a]["choice"]["options"]
        c=v["contrasts"][a]
        if cfg and v["gates"][a]["feasible"] and c["par2_ms"]["value"]<=.98 and c["par2_ms"]["ci"][1]<1 and c["paths"]["ci"][0]>=.98:
            good.append(a)
    if good:
        lead="На закрытом тесте ускорение с сохранением покрытия подтвердило плечо "+", ".join(NAMES[a] for a in good)+"."
    elif all(not v["arms"][a]["choice"]["options"] for a in ("smac","sol")):
        lead="Ни Sol, ни SMAC не выбрали настройку для замены cvc5 default по заданным порогам."
    else:
        lead="Закрытый тест не подтвердил ускорение cvc5 1.4.1 с сохранением покрытия."
    body=[]
    for a in ("smac","sol"):
        c=v["arms"][a]["choice"]
        m=v["contrasts"][a]
        novelty=sum(t["mask"] not in (0,8) for t in (read(LIVE/a/"training.json") if (LIVE/a/"training.json").exists() else []))
        note=("PAR2 между двумя исполнениями default характеризует шум и возникающую вариацию объёма поиска."
              if c["mask"]==0 else ("Изменения покрытия или надёжности вышли за заранее заданные пороги."
              if not v["gates"][a]["feasible"] else ("Тестовые условия выполнены." if a in good else
              "Предварительно выбранный вариант остался неубедительным по порогам величины или неопределённости.")))
        body.append(f'{NAMES[a]} измерил {c["new_trials"]} наборов, {novel} новых к прошлому живому сравнению, '
                    f'{c["feasible_train"]} допустимых на train. Заморожен {opts(c["options"])}. '
                    f'Тестовый PAR2 {interval(m["par2_ms"])} от default, пути {interval(m["paths"])}. {note}')
    return lead,body,good

def main():
    v=read(ROOT/"evidence/verification.json")
    start=(ROOT/"evidence/session-start-msk.txt").read_text().strip()
    end=stamp()
    lead,body,good=conclusion(v)
    session_min=duration(start,end)/60
    rng_note="На тесте четыре проектных кластера, поэтому даже проектный bootstrap даёт ограниченное представление о переносимости."
    sol_calls=v["arms"]["sol"]["inference"]
    successful=sum(c["completed"] for c in sol_calls)
    informed=sum(c["completed"] and c["inline_train"] for c in sol_calls)
    wider=interval(v["contrasts"]["z3"]["par2_ms"])
    cached="Совпадение замороженного варианта с default означает отдельные дублирующие JVM на тесте с теми же настройками."
    css="""
:root{color-scheme:light;--ink:#17252e;--muted:#4d6473;--line:#d4dedf;--sea:#075760;--paper:#f6f8f7;--white:#fff}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}header{background:#103943;color:#fff;padding:3rem max(1.2rem,calc((100vw - 1220px)/2)) 2rem}header p{max-width:70rem}header .meta{color:#c4dfe0;font-size:.94rem}main{max-width:1260px;margin:auto;padding:1rem 1.35rem 3rem}nav{padding:1rem 0;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:.6rem 1.25rem}h1{margin:.2rem 0 .7rem;line-height:1.12;font-size:clamp(2rem,5vw,3.5rem);max-width:55rem}h2{margin-top:2.8rem;line-height:1.2;font-size:1.55rem}p{max-width:100ch}a{color:var(--sea)}a:focus,summary:focus{outline:3px solid #cc7800;outline-offset:3px}.scroller{overflow-x:auto;margin:1rem 0 1.35rem;border:1px solid var(--line);border-radius:8px;background:var(--white)}table{font-size:.88rem;border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}td,th{text-align:left;padding:.47rem .65rem;border-bottom:1px solid var(--line);vertical-align:top}thead{background:#dae9e8}tbody tr:nth-child(even){background:#f5f9f8}td{min-width:4.2rem}td code,th code{overflow-wrap:anywhere}tbody th{font-weight:600}summary{cursor:pointer;color:var(--sea);font-weight:650;padding:.55rem 0}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.88em}.callout{border-left:4px solid #0a787e;padding:.55rem 1rem;background:#e6f1ef;margin:1.3rem 0}.note{color:var(--muted)}footer{margin-top:2rem;color:var(--muted);border-top:1px solid var(--line);padding-top:1rem}@media print{body{background:#fff}header{color:var(--ink);background:#fff;padding:1rem}header .meta{color:var(--muted)}nav{display:none}.scroller{overflow:visible}td,th{padding:.2rem .3rem}table{font-size:8pt}}
"""
    html=f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Настройки cvc5 1.4.1 в живом USVM — Sol и SMAC</title><style>{css}</style></head>
<body><header>
<p class="meta">butvinm-work · {start} — {end} · {d(session_min,1)} мин, МСК</p>
<h1>Поиск настройки cvc5 1.4.1 для живого USVM</h1>
<p><strong>{text(lead)}</strong></p></header><main>
<nav aria-label="Оглавление"><a href="#history">Диагностика</a><a href="#design">Методика</a>
<a href="#search">Поиск</a><a href="#test">Закрытый тест</a><a href="#verify">Проверка</a></nav>
<div class="callout">{''.join(f"<p>{text(p)}</p>" for p in body)}
<p>Z3 как внешний контроль: PAR2 {wider} от cvc5 default. {text(rng_note)}</p></div>
{historical()}
<h2 id="design">Методика</h2>
<p>Один рантайм USVM/KSMT с сертифицированным JNI cvc5 1.4.1. Штатная политика USVM продолжает
исследование после ошибки шага. Сохранялись path selector FORK_DEPTH, лимит исследования 6 с, лимит check 1,6 с,
лимит операции с типами 1 с, 4 000 шагов, стагнация 3 500 шагов. Два зерна выбора пути USVM (0 и 71), native seed 0.
Четыре работника на физических ядрах 12–15, один JVM на ядро, 1 500 MiB heap, 105 с внешнего wall на JVM,
сторож RSS 2 500 000 KiB. Только обычные функции библиотек и синтетические проверки корректности. Replay
не использовался для новых измерений.</p>
<p>Train: automaton, re2j, RoaringBitmap, commons-pool2 (32 метода). Validation: lz4-java,
generics-resolver, packageurl-java (20 методов). Test: hppc, lucene-core, jsoup, commons-validator (32 метода).
Проекты и целевые исходные классы разделены. JDK и инфраструктурные зависимости общие. Тестовые проекты ранее были представлены в инженерной диагностике.
Для теста классы прежних целей, включая внешние классы вложенных классов, исключены до измерений.
По четыре низко- и высоковетвящихся метода на проект получены из зафиксированного ASM-списка по солёному хешу.
Трудные методы оставались в назначениях. Пара обрабатывает одинаковые методы, их порядок, seed и ядро.
Четыре тестовых повтора дают каждой конфигурации каждую порядковую позицию и по два наблюдения на seed.</p>
<p>Обе стороны получили доступ к 32 комбинациям пяти переключателей. Контракт проверил SAT/UNSAT, модель
скаляра, FP и массива, чтение модели после закрытия, области, assumptions и unsat core; синтетический Java-метод
проверил достижимость трёх допустимых результатов. Цель — среднее время вызова анализатора с PAR2 16 с
за ошибку, пропуск, несверенный рантайм, UNKNOWN, потерю состояния при исключении шага или лимит
исследования. Допустимость требует ≥98% завершённых путей, рёбер и шагов в сумме и ≥95% путей на каждом проекте.
Среднее покрытие инструкций может снизиться максимум на 1 п.п. Число сбоев JVM, штрафных вызовов, ошибок шага
и UNKNOWN (в том числе таймаутов) не может расти. Для отбора дополнительно требуются ≥2% выигрыша PAR2 и ≤2% роста
полного wall. Для тестовых интервалов выборка по проектам сохраняет парность методов и повторов.</p>
<p><a href="PREREG.md">Предрегистрация</a> · <a href="inputs/seal.json">Хеши предрегистрации</a> ·
<a href="inputs/manifest.json">Сигнатуры и JAR</a> · <a href="inputs/schedule.json">Назначения</a> ·
<a href="inputs/space.json">Допуск комбинаций</a></p>
{searches(v)}
{report_results(v)}
<h2 id="verify">Проверка и ограничения</h2>
<p>Независимая программа заново связала вызовы с проектами, методами, ядрами и seed, сверила SHA-256 исходных
Java/JNI артефактов, карты загруженных библиотек, версии движка и Java, исходные и фактические опции cvc5 после
первого native check. Для Z3 сохранены JNI, версия и справка параметров заводской конфигурации.
Проверены {v["verified_jvms"]} из {v["jvms"]} запланированных JVM на этапах train, validation и test; синтетический
smoke отдельно охватил 66 JVM. Максимальный выборочный RSS одной живой JVM {d(v["max_rss_kib"]/1024,1)} MiB.
Первая компиляция overlay выявила тип seed Long; успешная вторая сборка сохранила тот же нативный рантайм.
Первый автоматический допуск отверг правильные Java URL из-за формата; исправленный сверщик подтвердил
те же исходные логи. Сырые данные обеих попыток сохранены.</p>
<p>Sol завершил {successful}/{len(sol_calls)} запросов, {informed} с новым train во входном потоке.
Первый вывод модели сделан без чтения train из-за ошибки инструмента. Добавление train в stdin восстановило
передачу результатов, сохранив изоляцию и ограничения. Обе версии обёртки и входы запросов сохранены. Выделенные окна ограничили число рассмотренных конфигураций.
Результат оценивает этот бюджет и пространство. У каждого оптимизатора одна последовательность предложений
(для SMAC seed 290141), тогда как два seed и повторения относятся к USVM. Разница числа предложений
отражает также затраты на вывод Sol и остаток окна. Раздельные baseline и последовательные окна
оставляют возможность временного дрейфа нагрузки на хосте. {text(cached)} {text(rng_note)}
Даже при сопоставимом числе путей отличия шагов, SMT-запросов, таймаутов и исключений позволяют говорить об
ускорении поведения всего live-клиента только вместе с наблюдавшимся объёмом исследования. Число вычислительно
завершённых путей не является исчерпывающей оценкой семантической эквивалентности.</p>
<p><a href="evidence/verification.json">Независимая сверка и хеши сырых логов</a> ·
<a href="evidence/test-by-method.json">Тестовые строки по повторам</a> · <a href="raw/live/test-lock.json">Заморозка</a> ·
<a href="raw/live/timeline.jsonl">Журнал фаз</a> · <a href="evidence/runtime.json">Хеши рантайма</a> ·
<a href="scripts/verify_live.py">Код сверки</a> ·
<a href="evidence/amendment-sol-stdin.json">Восстановление передачи train в Sol</a></p>
<footer>Самодостаточный HTML. Таблицы и оформление встроены. Файлы доказательств доступны по относительным ссылкам.
Справка о CLI: <a href="https://learn.chatgpt.com/docs/non-interactive-mode">неинтерактивный Codex</a>.</footer>
</main></body></html>"""
    pending=ROOT/"report.tmp"
    pending.write_text(html)
    pending.replace(ROOT/"report.html")
    short="\n".join([lead,"",*body,
        f"Z3 / cvc5 default: PAR2 {wider}; пути {interval(v['contrasts']['z3']['paths'])}.",
        f"Тест: 32 новых метода из 4 проектов × 4 повтора, по 128 вызовов на режим.",
        "Исторические 14–16%: 6 505 (default) и 6 677 (none) против 7 753 завершённых путей Z3, 367 пар из 480.",
        f"Сверено {v['verified_jvms']}/{v['jvms']} JVM; Sol завершил {successful}/{len(sol_calls)} запросов, {informed} с train во входе.",
        "",f"Сессия {start} — {end} МСК ({d(session_min,1)} мин). Настройки заморожены {v['lock']} МСК.",
        f"Артефакты: {ROOT/'report.html'}, {ROOT/'evidence/verification.json'}, {ROOT/'raw'}",""])
    pending=ROOT/"summary.tmp"
    pending.write_text(short)
    pending.replace(ROOT/"summary.txt")
    print(short)

if __name__=="__main__":
    main()
