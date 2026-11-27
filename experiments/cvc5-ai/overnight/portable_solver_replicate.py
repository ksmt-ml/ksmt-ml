"""Repeat the full-stream CLI comparison on different library projects."""
from concurrent.futures import ProcessPoolExecutor
import time

import portable_solver_control as first
from replay import CORES, ROOT, STOP, digest, load
from report import build, write_json
from stability_metrics import card_table, fmt_ci


def main():
    previous=load(first.SCOPE)
    first.BASE=ROOT/"runs/34-portable-z3-other"
    first.SCOPE=ROOT/"protocols/portable-z3-other.json"
    first.SOURCE=ROOT/"runs/31-minimal-transfer/independent-manifest.json"
    first.PROJECT_LIMIT=5
    first.REPEATS=6
    first.BASE.mkdir(exist_ok=True)
    plan=first.freeze()
    assert not {r["family"] for r in previous["inputs"]}.intersection(r["family"] for r in plan["inputs"])
    assert not {r["sha256"] for r in previous["inputs"]}.intersection(r["sha256"] for r in plan["inputs"])
    assert plan["solver_shas"]==previous["solver_shas"] and plan["options"]==previous["options"]
    assert first.smoke(plan)
    with ProcessPoolExecutor(max_workers=len(CORES)) as pool:
        pending=[pool.submit(first.worker,i,plan) for i in range(len(CORES))]
        for future in pending:
            future.result(timeout=max(1,min(165,STOP-time.time()-12)))
    r=first.evaluate(plan)
    z,c=(r["statuses"][arm]["solved"] for arm in ("Z3","cvc5_rewrite"))
    intro=f"На {r['programs']} полных потоках других проектов cvc5 с переписыванием дал {fmt_ci(r['effects']['cvc5_rewrite']['ratio'])} к Z3."
    write_json(ROOT/"evidence/34-portable-z3-other.json",{
        "title":"34 · Полный контроль Z3 на других библиотеках",
        "summary":intro,"done":"Проверено сравнение CLI на другой группе проектов.",
        "paragraphs":[
            f"{r['programs']} методов из {r['projects']} проектов, по {r['repeats']} повторов. Повторно использованы записи проверки одного флага с запросами модели и ядра. Сверены отсутствие пересечения с опытом 33, бинарники и настройки.",
            f"За одинаковые 1,5 с Z3 завершил {z} полных сеансов, cvc5 с переписыванием {c}. Число израсходованных попыток и другие исходы указаны в таблице. ДИ пересэмплирует проекты.",
            "В исходном полном SMT-LIB сохранены запросы get-model и ядра предположений. Live JNI в других опытах измеряет заново порождаемые пути."
        ],
        "tables":[card_table(f"По {r['programs']*r['repeats']} попыток на роль",
                    ["Роль","PAR2, с","К Z3 [95% ДИ]","Таймаут","Ошибки","Unknown","Несовпадения"],
                    [[a,f"{q['par2']:.3f}",fmt_ci(q["ratio"]),*[r["statuses"][a][k] for k in ("timeout","error","unknown","incorrect")]]
                     for a,q in r["effects"].items()])],
        "paths":["protocols/portable-z3-other.json","runs/34-portable-z3-other/VERIFY.json",
                 "runs/34-portable-z3-other/worker-{0..5}.jsonl"],
        "recommendation":"Сверять абсолютную производительность с целевым бэкендом при том же потоке и при живом анализе.",
        "next":"Сравнить абсолютную скорость и покрытие на живом клиенте с точной версией CLI или совместимым JNI.",
    })
    build()


if __name__=="__main__":
    main()
