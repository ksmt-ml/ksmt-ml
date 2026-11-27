"""Time SMT-LIB command classes within a real incremental process using echo markers."""
import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import random
import re
import selectors
import subprocess
import sys
import time

import numpy as np

from corpus import USVM
from portfolio import MENU, frac, group_draws
from replay import CAMPAIGN, CORES, PYTHON, ROOT, STOP, append, check_budget, digest, ev, guarded_popen, lines, load, log, now
from report import build, write_json
from search import execute_workers
from stability_metrics import B, card_table, fmt_ci

PORTABLE=os.environ.get("NIGHT_PROFILE_REAL")=="1"
BASE=ROOT/("runs/16b-portable-profile" if PORTABLE else "runs/11-profile")
SCOPE=ROOT/"protocols"/("profile-portable-real.json" if PORTABLE else "profile.json")
CAP=4.50
REPEATS=8
TYPES={"check-sat":"check","check-sat-assuming":"check","get-model":"model",
       "get-unsat-assumptions":"core","get-unsat-core":"core"}
ANSWER=re.compile(rb"^(sat|unsat|unknown)$")
MARK=re.compile(rb'^"?night_profile_([0-9]+)_([ab])"?$')

def input_mix():
    if PORTABLE:
        source=ROOT/"runs/12-new-libraries/portable-corpus/manifest.json"
        if not source.exists():
            from bridge import BASE as BRIDGE
            enc=load(BRIDGE/"replay/metrics.json")["next_encoding"]
            source=BRIDGE/enc/"paired-manifest.json"
        library=defaultdict(list)
        for r in load(source)["sessions"]:
            if r["operations"].get("model",0) and len(r["oracle"])>=4:
                library[r["family"]].append({**r,"origin":"портируемые библиотечные записи",
                    "frozen_file":os.path.relpath(source.parent/r["file"],ROOT)})
        out=[r for _,records in sorted(library.items()) for r in sorted(records,key=lambda v:v["sha256"])[:6]]
        assert len(out)>=8
        return out
    manifests=[ROOT/"runs/04-transfer/corpus/manifest.json",ROOT/"runs/10-representation/corpus/manifest.json",
               ROOT/"runs/02-portfolio/dev-corpus/manifest.json"]
    cohort=[]
    for v,manifest in enumerate(manifests):
        if not manifest.exists():
            continue
        data=load(manifest)
        pool=defaultdict(list)
        for r in data["sessions"]:
            if r["operations"].get("model",0)>=1 and len(r["oracle"])>=4:
                family=r["family"] if v==0 else f"{v}-{r['family']}"
                pool[family].append({**r,"family":family,"origin":("libraries","paired Java","development Java")[v],
                                      "frozen_file":os.path.relpath(manifest.parent/r["file"],ROOT)})
        if v==0:
            for f,rows in sorted(pool.items()):
                cohort.extend(sorted(rows,key=lambda r:r["sha256"])[:5])
        else:
            fam=sorted(pool,key=lambda f:pool[f][0]["sha256"])[:12 if v==1 else 8]
            cohort.extend(min(pool[f],key=lambda r:r["sha256"]) for f in fam)
    assert len(cohort)>=14
    return cohort

def init():
    BASE.mkdir(exist_ok=True)
    if SCOPE.exists():
        return load(SCOPE)
    selected=input_mix()
    menu=load(MENU)
    controls={"default":{},"global":menu["arms"][menu["global_arm"]],
              "joint":{"bv-sat-solver":"cadical","simplification":"none"}}
    rec={"locked":now(),"question":"Where in a complete session do flag effects arise, search/check or queried model/core construction?",
         "source":"reused portable library sessions. Exploratory attribution." if PORTABLE else
                      "reused generated and available real complete sessions. Exploratory attribution.",
         "admission":"check/model traces only; choose via method metadata and content hash, no cvc5 timing information",
         "repeats":REPEATS,"timeout_s":CAP,"options":controls,
         "common_arrays_exp":PORTABLE,
         "profile_readout":"echo boundaries per check/model/core. Each contiguous stdout-read gets one timestamp. The same process does all incremental work and ordinary stream is a randomized paired overhead control.",
         "overhead_rule":"If profiling overhead or significant timer coalescing matters, report observed profile as descriptive only.",
         "cohort":[{k:r[k] for k in ("group","family","origin","frozen_file","sha256")} for r in selected]}
    write_json(SCOPE,rec)
    SCOPE.chmod(0o444)
    prepare_inputs(selected)
    return rec

def prepare_inputs(selected):
    entries=[]
    dst=BASE/"inputs"
    dst.mkdir()
    sys.path.insert(0,str(USVM))
    from convert import render, terms
    for i,r in enumerate(selected):
        source=ROOT/r["frozen_file"]
        assert digest(source)==r["sha256"]
        nodes=terms(source.read_text())
        commands,tags=[],[]
        for node in nodes:
            op=node[0]
            body=render(node)
            if op in TYPES:
                tag=len(tags)
                tags.append(TYPES[op])
                commands.extend((f'(echo "night_profile_{tag}_a")',body,f'(echo "night_profile_{tag}_b")'))
            else:
                commands.append(body)
        target=dst/f"{i:03d}.smt2"
        target.write_text("\n".join(commands)+"\n")
        assert tags.count("check")==len(r["oracle"])
        entries.append({**r,"profile":str(target.relative_to(ROOT)),"profile_sha256":digest(target),"tags":tags})
    write_json(BASE/"inputs.json",entries)
    return entries

def measure(entry,params,deadline):
    limit=min(CAP,deadline-time.monotonic())
    if limit<=.005:
        raise TimeoutError("profiling deadline")
    path=ROOT/entry["profile"]
    cmd=["/usr/bin/prlimit",f"--as={ev.LIMIT_AS}","--core=0","--",ev.SOLVER,
         "--lang=smt2","--incremental","--quiet",f"--seed={ev.SOLVER_SEED}",*ev.flags(params),
         *(["--arrays-exp"] if PORTABLE else []),path]
    before=ev.child_cpu()
    start=time.monotonic()
    events,answers=[],[]
    marker_count=0
    timeout=False
    partial=b""
    errors=[]
    reader=selectors.DefaultSelector()
    p=guarded_popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try:
        reader.register(p.stdout,selectors.EVENT_READ,"stdout")
        reader.register(p.stderr,selectors.EVENT_READ,"stderr")
        while reader.get_map():
            remaining=limit-(time.monotonic()-start)
            if remaining<=0:
                timeout=True
                break
            ready=reader.select(timeout=remaining)
            if not ready:
                timeout=True
                break
            for key,_ in ready:
                data=os.read(key.fd,131072)
                stamp=time.monotonic()-start
                if not data:
                    reader.unregister(key.fileobj)
                    continue
                if key.data=="stderr":
                    if sum(map(len,errors))<4000:
                        errors.append(data[:4000])
                    continue
                strings=(partial+data).split(b"\n")
                partial=strings.pop()
                for line in strings:
                    event=MARK.fullmatch(line.strip())
                    if event:
                        events.append([int(event[1]),event[2].decode(),stamp])
                    elif ANSWER.fullmatch(line.strip()):
                        answers.append(line.strip().decode())
                    elif line.lstrip().startswith(b"(error"):
                        errors.append(line[:1000])
        if not timeout:
            remaining=limit-(time.monotonic()-start)
            try:
                p.wait(timeout=max(.001,remaining))
            except subprocess.TimeoutExpired:
                timeout=True
    finally:
        reader.close()
        if p.poll() is None:
            p.kill()
        p.wait()
        p.stdout.close()
        p.stderr.close()
    wall=time.monotonic()-start
    expected=entry["oracle"]
    bad=sum(a!=b and a!="unknown" for a,b in zip(answers,expected))
    if bad:
        status="incorrect"
    elif timeout or wall>CAP:
        status="timeout" if limit==CAP else "budget"
    elif p.returncode or len(answers)!=len(expected) or any(e.lstrip().startswith(b"(error") for e in errors):
        status="error"
    elif "unknown" in answers:
        status="unknown"
    else:
        status="solved"
    return {"status":status,"rc":p.returncode,"wall_s":wall,"solver_cpu_s":ev.child_cpu()-before,"answers":answers,
            "markers":events,"expected_markers":len(entry["tags"])*2,"wrong_answers":bad,
            "stderr":b"\n".join(errors).decode(errors="replace")[:4000],
            "par2":None if status=="budget" else wall if status=="solved" else 2*CAP}

def orders(settings,entries,slot,count=None):
    count=REPEATS if count is None else count
    choices=[(a,m) for a in settings for m in ("ordinary","profiled")]
    for repeat in range(slot,count,len(CORES)):
        rng=random.Random(484144+967939*repeat)
        order=list(entries)
        rng.shuffle(order)
        for r in order:
            candidate_order=list(choices)
            rng.shuffle(candidate_order)
            yield r,repeat,candidate_order

def worker(slot):
    os.sched_setaffinity(0,{CORES[slot]})
    settings=load(SCOPE)["options"]
    entries=load(BASE/"inputs.json")
    out=BASE/f"worker-{slot}.jsonl"
    assert not out.exists()
    deadline=time.monotonic()+min(3300,STOP-time.time()-8)
    for entry,repeat,choices in orders(settings,entries,slot):
        for position,(arm,mode) in enumerate(choices):
            if time.monotonic()>deadline:
                raise TimeoutError("profile replay deadline")
            if mode=="ordinary":
                file=ROOT/entry["frozen_file"]
                item={**entry,"file":file.name}
                if PORTABLE:
                    original=ev.flags
                    try:
                        ev.flags=lambda p:[*original(p),"--arrays-exp"]
                        result=ev.measure(file.parent/"unused-manifest.json",item,settings[arm],CAP,deadline)
                    finally:
                        ev.flags=original
                else:
                    result=ev.measure(file.parent/"unused-manifest.json",item,settings[arm],CAP,deadline)
            else:
                result=measure(entry,settings[arm],deadline)
            append(out,{"group":entry["group"],"family":entry["family"],"origin":entry["origin"],
                        "repeat":repeat,"position":position,"arm":arm,"mode":mode,"core":CORES[slot],**result})

def gate(entries,scope):
    path=BASE/"smoke.json"
    if path.exists():
        assert load(path)["ok"]
        return
    old=os.sched_getaffinity(0)
    result=[]
    try:
        os.sched_setaffinity(0,{CORES[0]})
        for item in sorted(entries,key=lambda p:len(p["oracle"]))[:5]:
            row=measure(item,{},time.monotonic()+6)
            if row["status"]=="solved":
                order=[(k,m) for k in range(len(item["tags"])) for m in ("a","b")]
                ok=[(x,y) for x,y,_ in row["markers"]]==order and row["answers"]==item["oracle"]
                result.append({"group":item["group"],"ok":ok,**row})
    finally:
        os.sched_setaffinity(0,old)
    ok=bool(result) and all(r["ok"] for r in result)
    write_json(path,{"ok":ok,"completed_probes":result})
    if not ok:
        raise RuntimeError("echo instrumentation failed on the smoke sessions")

def verify(scope,entries):
    case_by_group={r["group"]:r for r in entries}
    rows=[]
    for core in range(len(CORES)):
        actual=lines(BASE/f"worker-{core}.jsonl")
        expected=[(p["group"],r,i,a,m)
                  for p,r,choices in orders(scope["options"],entries,core)
                  for i,(a,m) in enumerate(choices)]
        assert [(r["group"],r["repeat"],r["position"],r["arm"],r["mode"]) for r in actual]==expected
        for r in actual:
            item=case_by_group[r["group"]]
            assert digest(ROOT/item["frozen_file"])==item["sha256"]
            assert digest(ROOT/item["profile"])==item["profile_sha256"]
            oracle=item["oracle"]
            assert r["wrong_answers"]==sum(a!=b and a!="unknown" for a,b in zip(r["answers"],oracle))
            if r["status"]=="solved":
                assert r["answers"]==oracle and r["wall_s"]<=CAP and r["rc"]==0
            assert r["status"]!="budget"
            assert r["par2"]==(r["wall_s"] if r["status"]=="solved" else 2*CAP)
            if r["mode"]=="profiled":
                marks=r["markers"]
                expect=[(i,k) for i in range(len(item["tags"])) for k in ("a","b")]
                assert [(x,y) for x,y,_ in marks]==expect[:len(marks)]
                assert all(t1<=t2 for (_,_,t1),(_,_,t2) in zip(marks,marks[1:]))
                if r["status"]=="solved":
                    assert len(marks)==r["expected_markers"]
            rows.append(r)
    write_json(BASE/"VERIFY.json",{"rows":len(rows),"programs":len(entries),"repeats":REPEATS,
               "options":list(scope["options"]),"validated":["paired order","manifest hashes","oracle","PAR2","timestamp order"]})
    return rows

def summarize(scope,entries,rows):
    case={r["group"]:r for r in entries}
    by={(r["group"],r["repeat"],r["arm"],r["mode"]):r for r in rows}
    tab=[]
    profile={}
    per_case={}
    for arm in scope["options"]:
        paired=defaultdict(list)
        kind_total=defaultdict(list)
        coalesced=0
        intervals=0
        for entry in entries:
            g=entry["group"]
            for rep in range(REPEATS):
                a=by[g,rep,arm,"ordinary"]
                b=by[g,rep,arm,"profiled"]
                if a["status"]=="solved" and b["status"]=="solved":
                    data=Counter()
                    for (i,_,start),(j,_,end) in zip(b["markers"][::2],b["markers"][1::2],strict=True):
                        assert i==j
                        data[entry["tags"][i]]+=end-start
                        intervals+=1
                        coalesced+=end==start
                    data["other"]=max(0,b["wall_s"]-sum(data.values()))
                    paired[g].append((a["wall_s"],b["wall_s"]))
                    for k in ("check","model","core","other"):
                        kind_total[g,k].append(data[k])
        g=[p for p in paired if paired[p]]
        g.sort()
        assert g
        X=np.array([np.mean(x,axis=0) for x in (paired[k] for k in g)])
        matrix=group_draws([case[k]["family"] for k in g],np.random.default_rng(41017))
        ratio=frac(X[:,1],X[:,0],matrix)
        category={name:float(np.mean([np.mean(kind_total[k,name]) for k in g])) for name in ("check","model","core","other")}
        missed=np.mean(X[:,1]-X[:,0])
        outcome_counts={mode:dict(Counter(r["status"] for r in rows if r["arm"]==arm and r["mode"]==mode))
                        for mode in ("ordinary","profiled")}
        profile[arm]={"both_solved_programs":len(g),"both_solved_pairs":sum(map(len,paired.values())),
                      "profile_vs_plain_wall_ratio":ratio,"mean_paired_overhead_s":float(missed),
                      "event_time_s":category,
                      "same_timestamp":coalesced,"intervals":intervals,"status":outcome_counts,
                      "per_program":{j:{"plain_s":float(X[i,0]),"profile_s":float(X[i,1]),
                                   "event_total_s":{k:float(np.mean(kind_total[j,k])) for k in category}}
                                   for i,j in enumerate(g)}}
        tab.append([arm,len(g),sum(map(len,paired.values())),fmt_ci(ratio),f'{1000*missed:+.1f}',
                    *[f'{1000*category[k]:.1f}' for k in ("check","model","core","other")],f"{coalesced}/{intervals}"])
    counts=Counter(r["origin"] for r in entries)
    families=len(set(r["family"] for r in entries))
    result={"programs":len(entries),"eligible_sources":counts,"families":families,"repeats":REPEATS,
            "rows":len(rows),"profiles":profile,"bootstrap":B}
    write_json(BASE/"metrics.json",result)
    ref=profile["default"]
    summary=(f"В {ref['both_solved_pairs']} полных парах default команды check и get-model заняли в среднем "
             f"{1000*ref['event_time_s']['check']:.1f} и {1000*ref['event_time_s']['model']:.1f} мс на программу.")
    failures=Counter(r["status"] for r in rows)
    name="16b-portable-profile.json" if PORTABLE else "12-profile.json"
    numbered="16б · Время моделей в полных библиотечных сеансах" if PORTABLE else "12 · Время проверок и построения моделей внутри сеанса"
    write_json(ROOT/"evidence"/name,{
        "title":numbered,
        "done":f"Сверены {len(rows)} парных записей обычного и инструментированного воспроизведения.",
        "summary":summary,
        "paragraphs":[
            f"Повторно использованы {len(entries)} ранее измеренных программ в {families} семействах. По источникам: " +
            ", ".join(f"{k} {v}" for k,v in counts.items()) + f". {REPEATS} повторов каждого режима. Сеансы отобраны по наличию модели и хотя бы четырёх проверок с хеш-порядком. Это разведочное измерение механизма.",
            "Перед и после каждой команды check, get-model и запроса ядра добавлены SMT-LIB echo. Почти одновременные маркеры могут прийти в одном блоке чтения. Точность отдельных коротких команд ограничена. У обычного контроля исходный файл, тот же случайный порядок и лимит 4,50 с.",
            f"Разложение использует только пары, решённые обоими способами. Для default время профилирования к обычному {fmt_ci(ref['profile_vs_plain_wall_ratio'])}. Запрос модели здесь измеряет время печати SMT-LIB модели, а не живой JNI detach.",
            ("Совместимый библиотечный формат фиксирован до этого опыта. Во всех обычных и инструментированных режимах общий --arrays-exp." if PORTABLE else
             "Эта диагностическая серия использует настройки основной генераторной выборки."),
            f"В полном наборе {failures['timeout']} таймаутов, {failures['unknown']} unknown, {failures['error']} ошибок и {failures['incorrect']} несовпадений. Все разобранные ответы и порядок маркеров сверены."
        ],
        "tables":[card_table(f"Вклад команд · {len(entries)} доступных программ, по {REPEATS} повторов на режим",
                  ["Флаги","Программ в полных парах","Полных пар","Профиль / контроль [95% ДИ]","Δ полного wall, мс",
                   "check, мс","get-model, мс","Ядра, мс","Остальное, мс","Совпавшие отметки / интервалы"],tab)],
        "paths":[str(SCOPE.relative_to(ROOT)),*[str((BASE/n).relative_to(ROOT)) for n in
                 ("inputs.json","smoke.json","worker-{0..5}.jsonl","VERIFY.json","metrics.json")]],
        "recommendation":"Сопоставлять затраты по фазам только вместе с контролем накладных расходов инструментирования и наличием таймаутов.",
        "next":"Проверить перенос на более трудных или совершенно новых библиотечных методах с критериями включения, зафиксированными до записи."
    })
    build()

def main():
    s=init()
    entries=load(BASE/"inputs.json")
    gate(entries,s)
    if not (BASE/"VERIFY.json").exists():
        commands=[([PYTHON,"-B",__file__,"worker",str(i)],BASE/f"worker-{i}.out") for i in range(len(CORES))]
        if any(execute_workers(commands,3500)):
            raise RuntimeError("profiling worker failed")
    rows=verify(s,entries)
    summarize(s,entries,rows)
if __name__=="__main__":
    if len(sys.argv)>1:
        worker(int(sys.argv[2]))
    else:
        main()
