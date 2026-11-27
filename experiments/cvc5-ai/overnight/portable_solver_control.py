"""Compare installed Z3 and cvc5 on complete identical portable command streams."""
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import gzip
import json
import os
import random
import subprocess
import time

import numpy as np

from arrays_options import SCOPE as ARRAY_SCOPE
from portfolio import frac, group_draws
from replay import CAMPAIGN, CORES, ROOT, STOP, append, check_budget, digest, ev, guarded_popen, lines, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci

BASE=ROOT/"runs/33-portable-z3"
SCOPE=ROOT/"protocols/portable-z3-control.json"
SOURCE=ROOT/"runs/23-rewrite-transfer/independent-manifest.json"
Z3=CAMPAIGN/".venv/bin/z3"
CAP=1.5
REPEATS=4
PROJECT_LIMIT=4
ARMS=("Z3","cvc5_default","cvc5_rewrite")


def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    pools=defaultdict(list)
    for entry in load(SOURCE)["sessions"]:
        ops=entry["operations"]
        if ops.get("model",0)>0 and ops.get("unsatCore",0)>0:
            pools[entry["family"]].append(entry)
    entries=[{**r,"file":str((SOURCE.parent/r["file"]).relative_to(ROOT))}
             for _,rows in sorted(pools.items()) for r in sorted(rows,key=lambda x:x["sha256"])[:PROJECT_LIMIT]]
    assert len(entries)>=12 and len({r["family"] for r in entries})>=3
    plan={"locked":now(),"inputs":entries,"manifest_sha256":digest(SOURCE),"repeats":REPEATS,
          "options":load(ARRAY_SCOPE)["configs"]["global_rewrite"],
          "solver_shas":{"Z3":digest(Z3),"cvc5":digest(ev.SOLVER)},
          "question":"Do the complete compatible streams support an efficient CLI alternative to Z3 including get-model and core requests?",
          "design":"Use unchanged portable command streams, all original check/model/core operations, 1.5 wall cap and equal virtual-memory cap. Randomize roles within each method and repeat on one fixed core. Separate from live KSMT treatment and check-only audit."}
    write_json(SCOPE,plan)
    SCOPE.chmod(0o444)
    return plan


def measure(arm,item,plan,cap):
    path=ROOT/item["file"]
    if arm=="Z3":
        cmd=[Z3,"-smt2",f"smt.random_seed={ev.SOLVER_SEED}",path]
    else:
        flags=ev.flags(plan["options"]) if arm=="cvc5_rewrite" else []
        cmd=[ev.SOLVER,"--lang=smt2","--incremental","--quiet",f"--seed={ev.SOLVER_SEED}","--arrays-exp",*flags,path]
    guard=min(cap,max(.001,STOP-time.time()-6))
    begin=time.monotonic()
    timed=False
    with guarded_popen(["/usr/bin/prlimit",f"--as={ev.LIMIT_AS}","--core=0","--",*cmd],
                       stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                       start_new_session=True) as worker:
        try:
            stdout,stderr=worker.communicate(timeout=guard)
        except subprocess.TimeoutExpired:
            timed=True
            worker.kill()
            stdout,stderr=worker.communicate()
        rc=worker.returncode
    wall=time.monotonic()-begin
    text=stdout.decode(errors="replace")
    err=stderr.decode(errors="replace")
    got=ev.ANSWERS.findall(text)
    want=item["oracle"]
    wrong=any(a!=b and a!="unknown" for a,b in zip(got,want)) or len(got)>len(want)
    if wrong:
        status="incorrect"
    elif timed or wall>cap:
        status="timeout"
    elif rc or "(error " in text or "error:" in err.lower():
        status="error"
    elif "unknown" in got:
        status="unknown"
    else:
        status="solved" if got==want else "error"
    return {"wall_s":wall,"status":status,"rc":rc,"timeout":timed,"par2":wall if status=="solved" else 2*cap,
            "answers":got,"stdout":text,"stderr":err}


def smoke(plan):
    p=BASE/"smoke"
    p.mkdir(exist_ok=True)
    prev=os.sched_getaffinity(0)
    tests={}
    try:
        os.sched_setaffinity(0,{CORES[0]})
        for arm in ARMS:
            probe=min(plan["inputs"],key=lambda x:len(x["oracle"]))
            obs=measure(arm,probe,plan,3)
            write_json(p/(arm+".json"),obs)
            tests[arm]=obs["status"]
    finally:
        os.sched_setaffinity(0,prev)
    passed=all(x=="solved" for x in tests.values())
    write_json(p/"VERIFY.json",{"ok":passed,"cases":tests})
    return passed


def worker(slot,plan):
    os.sched_setaffinity(0,{CORES[slot]})
    out=BASE/f"worker-{slot}.jsonl"
    raw=BASE/"raw"
    raw.mkdir(exist_ok=True)
    for idx in range(slot,len(plan["inputs"]),len(CORES)):
        item=plan["inputs"][idx]
        assert digest(ROOT/item["file"])==item["sha256"]
        for repeat in range(REPEATS):
            check_budget()
            order=random.Random(327811+503*idx+311*repeat).sample(ARMS,3)
            for pos,arm in enumerate(order):
                record=measure(arm,item,plan,CAP)
                file=raw/f"{idx:02d}-{repeat}-{arm}.json.gz"
                with gzip.open(file,"wt") as dest:
                    json.dump(record,dest)
                append(out,{"program":idx,"repeat":repeat,"arm":arm,"core":CORES[slot],"position":pos,
                            "raw":str(file.relative_to(ROOT)),"raw_sha256":digest(file),
                            **{k:v for k,v in record.items() if k not in ("stdout","stderr")}})


def evaluate(plan):
    raw=[r for p in sorted(BASE.glob("worker-*.jsonl")) for r in lines(p)]
    n=len(plan["inputs"])
    assert len(raw)==n*REPEATS*len(ARMS)
    cube=np.full((n,len(ARMS),REPEATS),np.nan)
    statuses={a:Counter() for a in ARMS}
    for rec in raw:
        i,r,a=rec["program"],rec["repeat"],rec["arm"]
        j=ARMS.index(a)
        assert rec["core"]==CORES[i%len(CORES)]
        assert random.Random(327811+503*i+311*r).sample(ARMS,3)[rec["position"]]==a
        assert digest(ROOT/rec["raw"])==rec["raw_sha256"]
        if rec["status"]=="solved":
            assert rec["answers"]==plan["inputs"][i]["oracle"] and rec["wall_s"]<=CAP
        assert rec["par2"]==(rec["wall_s"] if rec["status"]=="solved" else 2*CAP)
        assert np.isnan(cube[i,j,r])
        cube[i,j,r]=rec["par2"]
        statuses[a][rec["status"]]+=1
    groups=[r["family"] for r in plan["inputs"]]
    draw=group_draws(groups,np.random.default_rng(783371))
    x=cube.mean(axis=2)
    output={"programs":n,"projects":len(set(groups)),"repeats":REPEATS,"rows":len(raw),"statuses":statuses,
            "effects":{a:{"par2":float(x[:,i].mean()),"ratio":frac(x[:,i],x[:,0],draw)} for i,a in enumerate(ARMS)},
            "scope_sha256":digest(SCOPE)}
    write_json(BASE/"VERIFY.json",output)
    return output


def result(plan):
    output=evaluate(plan)
    n=output["programs"]
    statuses=output["statuses"]
    effect=output["effects"]["cvc5_rewrite"]["ratio"]
    intro=f"На {n} повторно использованных полных библиотечных потоках cvc5 с переписыванием дал PAR2 {fmt_ci(effect)} к установленному Z3."
    write_json(ROOT/"evidence/33-portable-z3.json",{
        "title":"33 · Контроль установленным Z3 на полных потоках",
        "summary":intro,"done":f"Сверены {output['rows']} полных запусков cvc5 и Z3 на одинаковых потоках.",
        "paragraphs":[
            f"Повторно использованы {n} методов из {output['projects']} проектов по {REPEATS} повторов. Один и тот же переносимый SMT-LIB сохраняет все команды модели и ядра. Предел 1,5 с, хеши решателей и потоков зафиксированы.",
            "Z3 запускается как отдельный CLI на тех же записанных данных. Обычный и настроенный cvc5 используют общий --arrays-exp. Это отличие от живого символьного клиента и отдельная проверка от аудита одних ответов.",
            "Количество таймаутов, ошибок и несовпадений указано по каждому режиму. Интервалы пересэмплируют целые проекты.",
        ],
        "tables":[card_table(f"По {n*REPEATS} полных потоков на режим",
                    ["Солвер","PAR2, с","К Z3 [95% ДИ]","Решено","Таймаут","Ошибка","Unknown","Несовпадение"],
                    [[a,f'{q["par2"]:.3f}',fmt_ci(q["ratio"]),*[statuses[a][k] for k in ("solved","timeout","error","unknown","incorrect")]]
                     for a,q in output["effects"].items()])],
        "paths":["protocols/portable-z3-control.json","runs/33-portable-z3/smoke/VERIFY.json",
                 "runs/33-portable-z3/VERIFY.json","runs/33-portable-z3/worker-{0..5}.jsonl"],
        "recommendation":"Оценивать замену бэкенда на одинаковых входах и отдельно на живом клиенте.",
        "next":"Для сравнимой версии cvc5 повторить контроль на целевой живой смеси библиотек.",
    })
    build()


def main():
    BASE.mkdir(exist_ok=True)
    plan=freeze()
    if not smoke(plan):
        raise RuntimeError("Same complete portable stream failed the Z3/cvc5 black-box smoke")
    with ProcessPoolExecutor(max_workers=len(CORES)) as pool:
        tasks=[pool.submit(worker,i,plan) for i in range(len(CORES))]
        for task in tasks:
            task.result(timeout=max(1,min(275,STOP-time.time()-12)))
    result(plan)


if __name__=="__main__":
    main()
