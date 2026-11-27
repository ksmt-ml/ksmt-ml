"""Check whether process startup changes C05 gains on short whole portable sessions."""
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import gzip
import json
import os
from pathlib import Path
import random
import re
import subprocess
import time

import numpy as np

from portfolio import MENU, frac, group_draws
from replay import CORES, ROOT, STOP, append, check_budget, digest, ev, guarded_popen, lines, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE=ROOT/"runs/21-reset-batches"
SCOPE=ROOT/"protocols/reset-batches.json"
SRC=ROOT/"runs/12-new-libraries/portable-corpus/manifest.json"
REPEATS=10
BATCH=3
MODES=("fresh/default","reset/default","fresh/C05","reset/C05")
CAP=1.50

def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    reference=ROOT/"runs/12-new-libraries/replay"
    manifest=load(SRC)
    p={m["group"]:m for m in manifest["sessions"]}
    menu=load(MENU)
    _,groups,arms,score,stat,wall=replay_cube(reference)
    idx=[arms.index(a) for a in ("default",menu["global_arm"])]
    bylib=defaultdict(list)
    for j,g in enumerate(groups):
        if np.all(stat[j,idx,:]=="solved") and float(np.max(wall[j,idx,:]))<.60:
            bylib[p[g]["family"]].append(g)
    sample=[]
    for k,g in sorted(bylib.items()):
        g=sorted(g,key=lambda name:p[name]["sha256"])[:9]
        sample.extend((k,g[i:i+BATCH]) for i in range(0,len(g)-BATCH+1,BATCH))
    assert len(sample)>=4
    record={"locked":now(),"hypothesis":"Persistent process execution may dilute or amplify the advantage measured on separate short solver processes.",
            "selection":"Reuse all-solved short portable library methods with max existing observation <0.60 s for both default and C05. Up to three disjoint batches of three per project, ranked by source hash.",
            "unit":"fixed disjoint batch of three complete sessions; preserve their continuity and all submitted operations. Within each batch a fresh-process arm launches separately, reset arm inserts SMT-LIB reset between episodes.",
            "conditions":"Both use shared arrays-exp, same seed and no change from the usual C05 flags. Actual reset retention of options, raw answers and a complete small batch verified first.",
            "timeouts":"1.50 s per standalone session, 4.50 s per whole persistent batch. Timing study on fast prior sessions; direct score is elapsed per batch, not whole-session PAR2.",
            "batch_order":"new random session order, then random four modes for each batch × repeat; same core for every mode in that block",
            "configs":{"default":{},"C05":menu["arms"][menu["global_arm"]]},
            "source_manifest_sha256":digest(SRC),"source_results_sha256":digest(reference/"VERIFY.json"),
            "programs":sum(len(x[1]) for x in sample),"batches":[{"family":k,"groups":g} for k,g in sample],"repeats":REPEATS}
    write_json(SCOPE,record)
    SCOPE.chmod(0o444)
    return load(SCOPE)

def run_cli(path,params,limit):
    args=["/usr/bin/prlimit",f"--as={ev.LIMIT_AS}","--core=0","--",ev.SOLVER,
          "--lang=smt2","--incremental","--quiet",f"--seed={ev.SOLVER_SEED}",
          "--arrays-exp",*ev.flags(params),path]
    t=time.monotonic()
    deadline=min(t+limit,t+STOP-time.time()-6)
    status="ok"
    with guarded_popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True) as p:
        try:
            out,err=p.communicate(timeout=max(.001,deadline-time.monotonic()))
            if p.returncode or b'(error "' in out:
                status="error"
        except subprocess.TimeoutExpired:
            p.kill()
            out,err=p.communicate()
            status="timeout"
    return {"status":status,"returncode":p.returncode,"wall_s":time.monotonic()-t,
            "stdout":out.decode(errors="replace"),"stderr":err.decode(errors="replace")}

def make_text(groups,raw):
    def convert(z):
        text=raw[z].strip()
        assert text.endswith("(exit)")
        return text.removesuffix("(exit)").rstrip()
    return "\n(reset)\n".join(convert(g) for g in groups)+"\n(exit)\n"

def smoke(s):
    path=BASE/"smoke"
    marker=path/"VERIFY.json"
    if marker.exists():
        assert load(marker)["ok"]
        return
    path.mkdir(parents=True)
    opts=["arrays-exp","simplification"]
    items={r["group"]:r for r in load(SRC)["sessions"]}
    labels=s["batches"][0]["groups"]
    script=path/"options.smt2"
    queries="\n".join(f"(get-option :{x})" for x in opts)
    script.write_text(queries+"\n(reset)\n"+queries+"\n(exit)\n")
    batch=path/"batch.smt2"
    batch.write_text(make_text(labels,{g:(SRC.parent/items[g]["file"]).read_text() for g in labels}))
    old=os.sched_getaffinity(0)
    try:
        os.sched_setaffinity(0,{CORES[0]})
        audit={}
        for a,p in s["configs"].items():
            options=run_cli(script,p,3)
            seen=options["stdout"].splitlines()
            case=run_cli(batch,p,4.5)
            expected=[a for g in labels for a in items[g]["oracle"]]
            correct=(case["status"]=="ok" and ev.ANSWERS.findall(case["stdout"])==expected)
            audit[a]={"options_retained":options["status"]=="ok" and len(seen)==4 and seen[:2]==seen[2:],
                      "options_status":options["status"],"options_stdout":seen,
                      "batch_answers_match":correct,"actual_case_status":case["status"]}
            (path/f"{a}-options.stdout").write_text(options["stdout"])
            (path/f"{a}-options.stderr").write_text(options["stderr"])
            (path/f"{a}-batch.stdout").write_text(case["stdout"])
            (path/f"{a}-batch.stderr").write_text(case["stderr"])
    finally:
        os.sched_setaffinity(0,old)
    ok=all(r["options_retained"] and r["batch_answers_match"] for r in audit.values())
    write_json(marker,{"ok":ok,"programs":len(labels),"repeats":1,"exact_checks":audit})
    if not ok:
        raise RuntimeError("persistent solver batch failed smoke before measurements")

def worker(slot,scope):
    os.sched_setaffinity(0,{CORES[slot]})
    raw=BASE/"raw"/f"slot-{slot}"
    raw.mkdir(parents=True,exist_ok=False)
    manifest=load(SRC)
    assert digest(SRC)==scope["source_manifest_sha256"]
    items={r["group"]:r for r in manifest["sessions"]}
    rows=BASE/f"worker-{slot}.jsonl"
    for n in range(slot,len(scope["batches"]),len(CORES)):
        batch=scope["batches"][n]
        source={g:items[g] for g in batch["groups"]}
        verify={g:(SRC.parent/r["file"]) for g,r in source.items()}
        assert all(digest(verify[g])==r["sha256"] for g,r in source.items())
        words={g:verify[g].read_text() for g in source}
        for r in range(REPEATS):
            check_budget()
            rng=random.Random(385126+n*991+r*37)
            order=list(source)
            rng.shuffle(order)
            choices=list(MODES)
            rng.shuffle(choices)
            expected=[a for g in order for a in source[g]["oracle"]]
            encoded=raw/f"{n:02d}-{r:02d}.smt2"
            encoded.write_text(make_text(order,words))
            for i,label in enumerate(choices):
                policy,key=label.split("/")
                params=scope["configs"][key]
                scripts=[encoded] if policy=="reset" else [verify[g] for g in order]
                results=[]
                for script in scripts:
                    raw_output=run_cli(script,params,CAP*BATCH if policy=="reset" else CAP)
                    results.append(raw_output)
                value=sum(r["wall_s"] for r in results)
                seen=[a for o in results for a in ev.ANSWERS.findall(o["stdout"])]
                matches=seen==expected
                statuses=Counter(o["status"] for o in results)
                a=label.replace("/","-")
                dest=raw/f"{n:02d}-{r:02d}-{a}.json.gz"
                with gzip.open(dest,"wt") as f:
                    json.dump(results,f)
                append(rows,{"batch":n,"repeat":r,"mode":label,"position":i,"core":CORES[slot],
                             "wall_s":value,"match":matches,"status_counts":statuses,
                             "observed":seen,"raw":str(dest.relative_to(ROOT)),"raw_sha256":digest(dest),
                             "groups_order":order,"script_sha256":digest(encoded)})

def analyze(s):
    raw=load(SRC)
    items={r["group"]:r for r in raw["sessions"]}
    n=len(s["batches"])
    observations={(r["batch"],r["repeat"],r["mode"]):r for p in BASE.glob("worker-*.jsonl") for r in lines(p)}
    assert len(observations)==n*REPEATS*len(MODES)
    K=len(MODES)
    scores=np.full((n,K,REPEATS),np.nan)
    counts=defaultdict(Counter)
    for i,batch in enumerate(s["batches"]):
        for r in range(REPEATS):
            rng=random.Random(385126+i*991+r*37)
            order=list(batch["groups"])
            rng.shuffle(order)
            choices=list(MODES)
            rng.shuffle(choices)
            expected=[a for g in order for a in items[g]["oracle"]]
            for position,a in enumerate(choices):
                log=observations[i,r,a]
                assert log["groups_order"]==order and log["position"]==position
                assert log["core"]==CORES[i%len(CORES)]
                assert digest(ROOT/log["raw"])==log["raw_sha256"]
                assert log["match"]==(log["observed"]==expected)
                if log["match"] and log["status_counts"].get("error",0)==0 and log["status_counts"].get("timeout",0)==0:
                    scores[i,MODES.index(a),r]=log["wall_s"]
                    counts[a]["complete"]+=1
                else:
                    counts[a]["failed"]+=1
    common=np.isfinite(scores).all(axis=1)
    valid=[i for i,k in enumerate(common) if k.any()]
    M=np.array([scores[j][:,common[j]].mean(axis=1) for j in valid])
    draws=group_draws([str(i) for i in valid],np.random.default_rng(713824))
    effects={}
    for arm,a,b in (("default",1,0),("C05",3,2)):
        stats={"reset_to_fresh":frac(M[:,a],M[:,b],draws),
               "ms_per_session_preserved":float((M[:,b]-M[:,a]).mean()*1000/BATCH)}
        effects[arm]=stats
    complete={k:dict(x) for k,x in counts.items()}
    result={"programs":s["programs"],"batches":n,"repeats":REPEATS,"physical_batches":len(observations),
            "complete_counts":complete,"common_batches":len(valid),"common_complete_pairs":int(common.sum()),
            "effects":effects}
    write_json(BASE/"metrics.json",result)
    write_json(BASE/"VERIFY.json",{"programs":s["programs"],"batch_rows":len(observations),"repeats":REPEATS,
               "checks":["paired order","input checksums","run checksums","answers","complete comparisons"]})
    a=effects["default"]
    title=f"Один процесс с reset экономил {a['ms_per_session_preserved']:.1f} мс на короткий сеанс default среди полных пар."
    write_json(ROOT/"evidence/22-reset-batches.json",{
        "title":"22 · Цена запуска процесса на коротком библиотечном сеансе",
        "done":"Проверены четыре режима полного последовательного воспроизведения с отдельными процессами и reset.",
        "summary":title,
        "paragraphs":[
            f"Повторно использованы {s['programs']} коротких библиотечных методов, сгруппированных в {n} непересекающихся троек. По {REPEATS} повторов, четыре режима в случайном порядке внутри блока. Источники — проверенные полные потоки из переносимого корпуса.",
            "Холодный режим запускает отдельный cvc5 для каждого сеанса. Общий процесс сбрасывает контекст командой reset и передаёт такой же поток с прежними начальными настройками. Быстрая проба подтвердила параметры и ответы после сброса.",
            f"При анализе учитываются общие полные повторы четырёх режимов, {len(valid)} троек и {int(common.sum())} блоков. Таймауты и ошибки указаны отдельно. Встроенные JAR-запуски из других экспериментов сюда не входят."
        ],
        "tables":[card_table("Издержка процесса · пары на тех же тройках",["Флаги","Reset / отдельные [95% ДИ]",
                      "Разница wall на сеанс, мс","Полных партий новый процесс","Полных партий reset",
                      "Неполных партий новый процесс","Неполных партий reset"],
                    [[k,fmt_ci(q["reset_to_fresh"]),f'{q["ms_per_session_preserved"]:+.1f}',
                      counts[f"fresh/{k}"]["complete"],counts[f"reset/{k}"]["complete"],
                      counts[f"fresh/{k}"]["failed"],counts[f"reset/{k}"]["failed"]] for k,q in effects.items()])],
        "paths":["protocols/reset-batches.json","runs/21-reset-batches/smoke/VERIFY.json","runs/21-reset-batches/raw/",
                 "runs/21-reset-batches/VERIFY.json","runs/21-reset-batches/metrics.json"],
        "recommendation":"Для коротких запусков учитывать, измеряется ли ускорение внутри решателя или устранение запуска процесса.",
        "next":"Проверить стоимость повторного использования контекстов в целевом живом клиенте с теми же лимитами."
    })
    build()

def main():
    s=freeze()
    BASE.mkdir(exist_ok=True)
    smoke(s)
    if not (BASE/"VERIFY.json").exists():
        active_slots=min(len(CORES),len(s["batches"]))
        with ProcessPoolExecutor(max_workers=active_slots) as pool:
            tasks=[pool.submit(worker,i,s) for i in range(active_slots)]
            for t in tasks:
                t.result(timeout=800)
    analyze(s)
if __name__=="__main__":
    main()
