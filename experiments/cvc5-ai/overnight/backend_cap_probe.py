"""Calibrate the full-stream cvc5/Z3 relation under two matched caps."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import gzip
import json
import os
import random
import time

import numpy as np

import portable_solver_control as control
from portfolio import frac, group_draws
from replay import CORES, ROOT, STOP, append, digest, lines, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci

BASE=ROOT/"runs/35-backend-caps"
SCOPE=ROOT/"protocols/backend-caps.json"
CAPS=(1.5,3.0)
RUNS=("Z3","cvc5_rewrite")
R=2
CONDITIONS=tuple((c,a) for c in CAPS for a in RUNS)


def prepare():
    p=[ROOT/"protocols"/name for name in ("portable-z3-control.json","portable-z3-other.json")]
    old,new=map(load,p)
    assert old["solver_shas"]==new["solver_shas"] and old["options"]==new["options"]
    items=old["inputs"]+new["inputs"]
    assert len({row["sha256"] for row in items})==len(items)
    plan={"locked":now(),"inputs":items,"options":old["options"],"caps_s":CAPS,"repeats":R,
          "source_certificates":{str(x.relative_to(ROOT)):digest(x) for x in p},
          "scope":"Reused portable methods, equally paired caps and roles within each block, new timings. Full commands including model and cores preserved.",
          "primary":"Ratio of cvc5 rewrite to Z3 PAR2 at each cap. Scope is the same corpus with two previously established options, whole-project uncertainty."}
    write_json(SCOPE,plan)
    SCOPE.chmod(0o444)
    return plan


def order(i,r):
    return random.Random(117531+i*419+r*93).sample(CONDITIONS,len(CONDITIONS))


def run(slot,plan):
    os.sched_setaffinity(0,{CORES[slot]})
    out=BASE/f"worker-{slot}.jsonl"
    for i in range(slot,len(plan["inputs"]),len(CORES)):
        case=plan["inputs"][i]
        assert digest(ROOT/case["file"])==case["sha256"]
        for r in range(R):
            for pos,(cap,arm) in enumerate(order(i,r)):
                assert time.time()<STOP-8
                obs=control.measure(arm,case,plan,cap)
                path=BASE/"raw"/f"{i:02d}-{r}-{pos}.json.gz"
                with gzip.open(path,"wt") as f:
                    json.dump(obs,f)
                append(out,{"i":i,"r":r,"pos":pos,"core":CORES[slot],"cap":cap,"arm":arm,
                            "score":obs["par2"],"status":obs["status"],"output_sha256":digest(path)})


def analyse(plan):
    n=len(plan["inputs"])
    rows=[r for f in BASE.glob("worker-*.jsonl") for r in lines(f)]
    assert len(rows)==n*R*len(CONDITIONS)
    A=np.full((n,len(CONDITIONS),R),np.nan)
    states={condition:Counter() for condition in CONDITIONS}
    for row in rows:
        i,r,j=row["i"],row["r"],row["pos"]
        cond=(row["cap"],row["arm"])
        assert cond==order(i,r)[j] and row["core"]==CORES[i%len(CORES)]
        path=BASE/"raw"/f"{i:02d}-{r}-{j}.json.gz"
        assert digest(path)==row["output_sha256"]
        with gzip.open(path,"rt") as f:
            raw=json.load(f)
        status=raw["status"]
        assert status==row["status"] and raw["par2"]==row["score"]
        if status=="solved":
            assert raw["answers"]==plan["inputs"][i]["oracle"] and 0<raw["wall_s"]<=cond[0]
        penalty=raw["wall_s"] if status=="solved" else 2*cond[0]
        alias=CONDITIONS.index(cond)
        assert penalty==row["score"] and np.isnan(A[i,alias,r])
        A[i,alias,r]=penalty
        states[cond][status]+=1
    vals=A.mean(axis=2)
    fam=[case["family"] for case in plan["inputs"]]
    draws=group_draws(fam,np.random.default_rng(95183))
    per={}
    for cap in CAPS:
        z,c=(CONDITIONS.index((cap,k)) for k in RUNS)
        per[str(cap)]={"ratio":frac(vals[:,c],vals[:,z],draws),
                       "means_s":{k:float(vals[:,CONDITIONS.index((cap,k))].mean()) for k in RUNS},
                       "statuses":{k:states[cap,k] for k in RUNS}}
    res={"programs":n,"projects":len(set(fam)),"repeats":R,"rows":len(rows),"caps":per,
         "scope_sha256":digest(SCOPE)}
    write_json(BASE/"VERIFY.json",res)
    summary=f"На {n} одинаковых записанных методах cvc5/Z3 составило {fmt_ci(per['1.5']['ratio'])} при 1,5 с и {fmt_ci(per['3.0']['ratio'])} при 3 с."
    write_json(ROOT/"evidence/35-backend-caps.json",{
        "title":"35 · Предел полного сеанса для контроля Z3 и cvc5",
        "summary":summary,"done":"Сверены два предела на тех же полных потоках двух решателей.",
        "paragraphs":[
            f"Повторно использованы {n} записей из {res['projects']} проектов двух последних CLI-проверок. {R} новых повтора на сочетание лимита и решателя, {len(rows)} запусков. Полные потоки сохраняют запросы модели и ядра.",
            "Порядок случайный внутри общего блока. PAR2 сравнивается при одинаковых пределах и удвоенном штрафе, отдельно для каждого предела. Доверительный интервал пересэмплирует проекты."
        ],
        "tables":[card_table(f"По {n*R} попыток на предел и решатель",
            ["Лимит, с","Решатель","PAR2, с","Настроенный cvc5/Z3","Таймауты","Ошибки","Unknown","Несовпадения"],
            [[cap,k,f"{q['means_s'][k]:.3f}",fmt_ci(q["ratio"]) if k=="cvc5_rewrite" else "1,000",
             *[q["statuses"][k][j] for j in ("timeout","error","unknown","incorrect")]] for cap,q in per.items() for k in RUNS])],
        "paths":["protocols/backend-caps.json","runs/35-backend-caps/VERIFY.json","runs/35-backend-caps/worker-{0..5}.jsonl"],
        "recommendation":"Сохранять Z3 как сквозной контроль в целевом живом эксперименте, когда сравниваются разные бэкенды.",
        "next":"Измерить на живых методах с той же версией cvc5, фиксированными процессорами и полной статистикой покрытия.",
    })
    build()


def main():
    BASE.mkdir()
    (BASE/"raw").mkdir()
    plan=prepare()
    with ProcessPoolExecutor(max_workers=len(CORES)) as pool:
        futures=[pool.submit(run,i,plan) for i in range(len(CORES))]
        for future in futures:
            future.result(timeout=max(1,min(175,STOP-time.time()-12)))
    analyse(plan)


if __name__=="__main__":
    main()
