"""Retest the live native comparison with a less restrictive outer JVM deadline."""
from concurrent.futures import ProcessPoolExecutor
import time

import native_variant as live
from replay import CORES, ROOT, STOP, digest, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci

BASE=ROOT/"runs/25b-native-long-jvm"
SCOPE=ROOT/"protocols/native-long-jvm.json"
SOURCE=live.BASE
SOURCE_SCOPE=live.SCOPE
CAP=75
REPEATS=2


def prepare():
    if SCOPE.exists():
        return load(SCOPE)
    earlier=load(SOURCE/"VERIFY.json")
    scope=load(SOURCE_SCOPE)
    assert min(row["missing"]/earlier["planned_per_arm"] for row in earlier["counters"].values())>.25
    smoke=SOURCE/"smoke/VERIFY.json"
    assert load(smoke)["ok"]
    run={**scope,"locked":now(),"repeats":REPEATS,
         "guards":{**scope["guards"],"jvm_case_s":CAP},
         "prior_measurements_sha256":digest(SOURCE/"VERIFY.json"),"prior_scope_sha256":digest(SOURCE_SCOPE),
         "black_box_smoke_sha256":digest(smoke),
         "repair":"Repeat all old method tags with longer outer JVM cap. Same runtime, TableIte, package, methods per JVM, method cap 6.0s and check cap 1.6s. Two new replays. Primary: intended paired completion count and diagnosed absence, then conditional wall and coverage. Missing from previous study triggers whole protocol; method selection remains unaltered."}
    write_json(SCOPE,run)
    SCOPE.chmod(0o444)
    return run


def publish(r):
    old=load(SOURCE/"VERIFY.json")
    same=r["paired_runs"]
    d=r["counters"]
    text=f"При увеличенном лимите JVM, переписывание/default дало {fmt_ci(r['wall_ratio'])} по полному времени на {same} парах."
    write_json(ROOT/"evidence/25b-native-long-jvm.json",{
        "title":"25б · Проверка живого результата с более длинным лимитом JVM",
        "summary":text,"done":"Повторено сравнение нативных опций с увеличенным внешним лимитом.",
        "paragraphs":[
            f"Повторно использованы все {r['programs']} методов из {r['projects']} проектов. По {REPEATS} новых повтора, суммарно {r['planned_per_arm']} запланированных вызовов на режим. JVM ограничена 75 с вместо 39. Исходные пределы метода 6 с и проверки 1,6 с сохранены.",
            f"В первой серии было {old['paired_runs']} завершённых пар из {old['planned_per_arm']}; в более длинной серии {same} из {r['planned_per_arm']}. Признак параметров подтверждался в каждой JVM, выдавшей метод со статусом ok.",
            f"Разность покрытия на полных парах {fmt_ci(r['coverage_difference_pp'])} п.п. ДИ по проектам. Показатель полного времени условен по завершению; доля отсутствий и внешние прерывания указаны отдельно.",
            (f"Отношение времени проверок {fmt_ci(r['check_ratio'])}." if r["check_ratio"] else
             "Для отношения времени проверок недостаточно ненулевых отметок по проектам."),
        ],
        "tables":[card_table(f"Живой USVM · по {r['planned_per_arm']} запланированных вызовов на режим",
            ["Режим","Статус ok","Без записи","Ошибка метода","Таймаут JVM","Прочая ошибка JVM"],
            [[a,row["ok"],row["missing"],sum(v for k,v in row.items() if k.startswith("error:")),
              row["jvm_timeout"],row["jvm_error"]] for a,row in d.items()])],
        "paths":["protocols/native-long-jvm.json","runs/25-native-rewrite/VERIFY.json",
                 "runs/25b-native-long-jvm/VERIFY.json","runs/25b-native-long-jvm/<проект>/<серия>/<режим>/java.log"],
        "recommendation":"Использовать внешний лимит, допускающий запуск JVM и работу клиента в заданных внутренних пределах. Считать отсутствия как отдельные исходы.",
        "next":"Сравнить скорость того же практического режима с cvc5 1.4.1 на живом целевом клиенте.",
    })
    build()


def main():
    plan=prepare()
    live.compile_overlay()
    live.BASE=BASE
    live.SCOPE=SCOPE
    live.R=REPEATS
    live.JVM_CAP=CAP
    BASE.mkdir(exist_ok=True)
    with ProcessPoolExecutor(max_workers=len(CORES)) as pool:
        tasks=[pool.submit(live.measure,core,plan) for core in CORES]
        for task in tasks:
            task.result(timeout=max(1,min(1080,STOP-time.time()-12)))
    publish(live.analyse(plan))


if __name__=="__main__":
    main()
