"""Measure a graduated real-budget comparison only when the prior live gate holds."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
import importlib.util
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"screening/src"))
sys.path.insert(0,str(TOP/"followup/src"))
import screen
from warm import base,run_batch,observe,compile_driver,CUTOFF
from screen import job,FIELDS
from analyze_power import STATS_EQUAL,cluster_ratio
import optimizer

DURATION=640
VALIDATION=280
ACQUIRE_CAP=105
METHODS=("smac","sol","random")
COLD=False

def equivalent(a,b):
    return bool(a["status"]==b["status"]=="ok" and
                all(a["stats"][k]==b["stats"][k] for k in STATS_EQUAL))

def gate():
    bench=TOP/"screening/evidence/warm-compact"
    rows=[]
    for f in sorted(bench.glob("*.json")):
        p=base.read(f)
        if not p["complete"] or not p["job"]["trace"]:
            continue
        separate=defaultdict(dict)
        for x in p["records"]:
            separate[x["tag"]][x["arm"]]=x
        for tag,mat in separate.items():
            a,b=(mat[k] for k in ("A","B"))
            if a["failed"] or b["failed"] or a["checks"]<5 or not equivalent(a,b):
                continue
            if a["stats"]["work_sha256"]!=b["stats"]["work_sha256"]:
                continue
            rows.append(dict(project=p["job"]["project"],tag=tag,job=p["job"]["id"],options=mat))
    catalog={}
    for arm in ("no-simpl","bv-eager","array-index","bv-bool","no-static"):
        by=defaultdict(lambda:[0.0,0.0])
        check=analysis=ref_check=ref_analysis=0.0
        shared=0
        for r in rows:
            q=r["options"][arm]
            a,b=[r["options"][k] for k in ("A","B")]
            if not q["failed"] and equivalent(q,a) and q["stats"]["work_sha256"]==a["stats"]["work_sha256"]:
                shared+=1
            ref=(a["check_ms"]+b["check_ms"])/2
            ref_check+=ref
            check+=q["check_ms"]
            ref_analysis+=(a["analyze_ms"]+b["analyze_ms"])/2
            analysis+=q["analyze_ms"]
            by[r["project"]][0]+=q["check_ms"]
            by[r["project"]][1]+=ref
        projects_won=sum(x<.95*y for x,y in by.values())
        ratios=[check/ref_check if ref_check else None,analysis/ref_analysis if ref_analysis else None]
        ok=(len(by)>=2 and shared==len(rows) and ratios[0] is not None and ratios[0]<=.95 and ratios[1]<=1.02
            and projects_won>=2)
        catalog[arm]=dict(n=len(rows),shared=shared,projects=len(by),projects_won=projects_won,
            check_ratio=ratios[0],analysis_ratio=ratios[1],reference_share=ref_check/ref_analysis if ref_analysis else None,
            project_check_ms=by,pass_gate=ok,value=screen.ARMS[arm][1])
    allowed={k:v for a in catalog.values() if a["pass_gate"] for k,v in a["value"].items()}
    stable={}
    for r in rows:
        tag=r["tag"]
        if tag not in stable:
            stable[tag]=[r["project"],0]
        stable[tag][1]+=r["options"]["A"]["checks"]
    result=dict(at=base.now(),gate=len(allowed)>=2,options=allowed,catalog=catalog,stable=stable)
    dest=ROOT/"evidence/live-gate.json"
    dest.parent.mkdir(exist_ok=True)
    if not dest.exists():
        base.write(dest,result)
    else:
        prior=base.read(dest)
        assert {k:v for k,v in prior.items() if k!="at"}=={k:v for k,v in result.items() if k!="at"}
    return result

def frozen(g):
    folder=ROOT/"inputs"
    folder.mkdir(exist_ok=True)
    dest=folder/"live.json"
    if dest.exists():
        return base.read(dest)
    source=TOP/"screening/inputs/research-split.json"
    p=base.read(source)["splits"]
    if g.get("source")=="08b":
        stable_plan=base.read(TOP/"stable/inputs/schedule.json")
        p["train"]=[dict(project=label,**entry) for label,entry in stable_plan["projects"].items()]
        p["train"]=sorted(p["train"],key=lambda row:-sum(v[1] for v in g["stable"].values() if v[0]==row["project"]))[:4]
    by=defaultdict(list)
    for tag,(proj,checks) in g["stable"].items():
        by[proj].append((checks,tag))
    chosen={}
    for step,ps in p.items():
        chosen[step]=[]
        for project in ps:
            tag=project["project"]
            if step=="train":
                if not by[tag]:
                    continue
                select=[max(by[tag])[1]]
            elif step=="validation":
                select=[m["tag"] for m in project["methods"][:2]]
            else:
                select=[m["tag"] for m in project["methods"]]
            chosen[step].append(dict(project,selected=select))
    assert len(chosen["train"])>=2 and len(chosen["test"])>=4
    protocol=TOP/"protocols"/("08c-cold-search.md" if g.get("source")=="08b" else "08-live-search.md")
    data=dict(at=base.now(),source_sha=base.sha(source),protocol_sha=base.sha(protocol),splits=chosen,values=g["options"],
        arm_order=[["smac","sol","random"],["sol","random","smac"],["random","smac","sol"]],
        seed=[20261123,23112321,120261123],traintime=DURATION,
        validationtime=VALIDATION)
    base.write(dest,data)
    return data

def prepare_pairs(where,projects,opts,seed,rep,compiled,mode,seconds,trace=False):
    if COLD:
        return cold_pairs(where,projects,opts,seed,rep,mode,seconds,trace)
    where.mkdir(parents=True,exist_ok=True)
    plan=where/"assignment.json"
    rng=random.Random(seed+rep+711)
    schedule=[]
    if plan.exists():
        schedule=base.read(plan)["schedule"]
    else:
        for i,p in enumerate(projects):
            keys=list(opts)
            rng.shuffle(keys)
            name=p["project"]
            scope=p["selected"]
            for n in range(0,len(scope),2):
                tags=scope[n:n+2]
                all_cells=[]
                for j,tag in enumerate(tags):
                    shift=(i+rep+j)%len(keys)
                    all_cells.extend((tag,a) for a in keys[shift:]+keys[:shift])
                schedule.append(job(f"{name}-g{n//2+1}",name,(0,71)[rep%2],base.CPUS[(i+rep+n//2)%4],trace,1500,
                                    "fixed",{a:("FORK_DEPTH",v) for a,v in opts.items()},all_cells))
        base.write(plan,dict(at=base.now(),schedule=schedule,opts=opts,mode=mode))
    assert base.read(plan)["opts"]==opts
    deadline=min(time.monotonic()+seconds,CUTOFF-time.time()+time.monotonic())
    pmap={p["project"]:p for p in projects}
    def lane(core):
        found={}
        for j in schedule:
            if j["core"]!=core:
                continue
            dest=where/j["id"]
            if dest.exists() and not (dest/"status.json").is_file():
                dest.rename(where/f'{j["id"]}-interrupted-{int(time.time())}')
            if not dest.exists():
                cap=min(ACQUIRE_CAP,deadline-time.monotonic()-3)
                if cap<17:
                    continue
                base.ensure_disk()
                run_batch(dest,j,pmap[j["project"]],compiled,cap=cap,heap="1250m",rss_limit=3_450_000)
            found[j["id"]]=observe(dest,j)
        return found
    with ThreadPoolExecutor(4) as pool:
        merged={k:v for lane in pool.map(lane,base.CPUS) for k,v in lane.items()}
    output=dict(schedule=schedule,results=merged,end=base.now())
    base.write(where/"result.json",output)
    return output

def summarize(data,arm,base_label="A"):
    complete=(len(data["schedule"])==len(data["results"]) and all(r["complete"] for r in data["results"].values()))
    records=[]
    by_project=defaultdict(list)
    for j in data["schedule"]:
        if j["id"] not in data["results"]:
            continue
        row=defaultdict(dict)
        for r in data["results"][j["id"]]["records"]:
            row[r["tag"]][r["arm"]]=r
        for tag,d in row.items():
            a,b=[d[a] for a in (arm,base_label)]
            m={k:[a[k],b[k]] for k in FIELDS}
            m.update(project=j["project"],tag=tag,equal=equivalent(a,b),
                     trace_equal=bool(a["stats"].get("work_sha256") and
                       a["stats"].get("work_sha256")==b["stats"].get("work_sha256")))
            records.append(m)
            by_project[j["project"]].append(m)
    sums={k:[sum(m[k][i] for m in records) for i in (0,1)] for k in FIELDS}
    def ratio(k):
        x,y=sums[k]
        return x/y if y else (1.0 if x==0 else float(x)+1)
    n=max(1,len(records))
    failure=[k for k in ("failed","step_errors","unknown","solver_timeouts","missing") if sums[k][0]>sums[k][1]]
    for k in ("paths","edges","steps"):
        if ratio(k)<.98:
            failure.append(k)
    for p,rr in by_project.items():
        if sum(r["paths"][0] for r in rr)<.95*sum(r["paths"][1] for r in rr):
            failure.append("project paths "+p)
    if sums["coverage"][0]<sums["coverage"][1]-n:
        failure.append("coverage")
    same=sum(m["equal"] for m in records)
    if not complete:
        failure.append("incomplete")
    if same/n<.95:
        failure.append("same work")
    if ratio("analyze_ms")>1.02:
        failure.append("analysis time")
    def snapshot(i):
        named=("checks","par2_ms","check_ms","analyze_ms","steps","paths","coverage","failed","step_errors","unknown","solver_timeouts")
        sub={k:round(sums[k][i],4) for k in named}
        anon={f"P{a+1}":{k:round(sum(m[k][i] for m in r),4) for k in named}
              for a,(p,r) in enumerate(sorted(by_project.items()))}
        return {**sub,"same_count":same,"n":len(records),"by_project":anon}
    return dict(complete=complete,feasible=not failure,passes=not failure and ratio("check_ms")<=.95,
                violations=failure,cost=ratio("check_ms")+(10+len(failure) if failure else 0),
                ratios={k:ratio(k) for k in ("par2_ms","analyze_ms","check_ms","coverage","paths","steps","edges")},
                train=snapshot(0),control=snapshot(1),sums=sums,records=records)

def validate(where,pool,advanced,values,seed,rep,compiled):
    final=where/"validation.json"
    if final.exists():
        return base.read(final)
    begin=time.monotonic()
    start=base.now()
    out=[]
    for i,mask in enumerate(advanced,1):
        remaining=VALIDATION-(time.monotonic()-begin)
        if remaining<ACQUIRE_CAP+3:
            break
        pair=prepare_pairs(where/"validation"/str(i),pool,{"A":{},"X":optimizer.options(mask,values)},
                           seed,rep,compiled,"validation",min(remaining,175 if COLD else ACQUIRE_CAP+3))
        out.append(dict(mask=mask,result=summarize(pair,"X")))
    best=min((s for s in out if s["result"]["passes"]),default=None,key=lambda s:s["result"]["cost"])
    chosen=best["mask"] if best else 0
    result=dict(start=start,end=base.now(),wall_s=time.monotonic()-begin,chosen=chosen,results=out)
    base.write(final,result)
    return result


def certify(where,allowed,values):
    where.mkdir(exist_ok=True)
    out=where/"certified.json"
    if out.exists():
        return base.read(out)["passed"]
    expected=base.read(base.EXPECTED)
    def lane(cpu):
        tested=[]
        for i,mask in enumerate(allowed):
            if i%4+12!=cpu:
                continue
            record=dict(mask=mask,config=optimizer.options(mask,values),passes=True)
            for mode,msg in (("contract","CONTRACT_PASS"),("smoke","LIVE_SMOKE_PASS")):
                path=where/f"mask-{mask:02d}"/mode
                if not path.exists():
                    base.case(path,cpu,"default",record["config"],0,mode=mode,cap=58)
                try:
                    state=base.read(path/"status.json")
                    assert state["returncode"]==0 and not state["watchdog"]
                    assert msg in (path/"stdout.log").read_text()
                    base.validate_backend(path,base.read(path/"backend.json"),base.read(path/"invocation.json"),expected)
                except (AssertionError,FileNotFoundError,KeyError) as e:
                    record["passes"]=False
                    record["reason"]=repr(e)
                    break
            tested.append(record)
        return tested
    with ThreadPoolExecutor(4) as pool:
        data=[r for rs in pool.map(lane,base.CPUS) for r in rs]
    passed=sorted(r["mask"] for r in data if r["passes"])
    assert 0 in passed
    base.write(out,dict(at=base.now(),passed=passed,records=data))
    return passed


def cold_gate():
    source=TOP/"stable/evidence/summary.json"
    old=base.read(source)
    rank={}
    for r in old["results"]["B"]["shared"]:
        key=r["tag"]
        x=rank.setdefault(key,[r["project"],0])
        x[1]+=r["checks"][0]
    values={k:v for arm in old["admission"] for k,v in old["arms"][arm].items()}
    gate=dict(at=base.now(),gate=len(values)>=2,options=values,stable=rank,
              source="08b",input_sha=base.sha(source))
    out=ROOT/"evidence/live-gate.json"
    out.parent.mkdir(exist_ok=True)
    if not out.exists():
        base.write(out,gate)
    return gate

def cold_pairs(where,projects,opts,seed,rep,mode,seconds,trace):
    module_path=TOP/"stable/src/measure.py"
    loader=importlib.util.spec_from_file_location("cold_pair_measure",module_path)
    runner=importlib.util.module_from_spec(loader)
    loader.loader.exec_module(runner)
    where.mkdir(parents=True,exist_ok=True)
    pmap={p["project"]:p for p in projects}
    rng=random.Random(seed+rep+103)
    result_path=where/"result.json"
    if result_path.exists():
        return base.read(result_path)
    schedule=[]
    for i,project in enumerate(projects):
        name=project["project"]
        order=list(opts)
        rng.shuffle(order)
        schedule.append(dict(id=name,project=name,core=base.CPUS[(i+rep)%4],seed=(0,71)[rep%2],
           tags=list(project["selected"]),trace=trace,order=order))
    base.write(where/"assignment.json",dict(at=base.now(),schedule=schedule,opts=opts,mode=mode))
    deadline=min(time.time()+seconds,CUTOFF)
    def lane(core):
        res={}
        for j in schedule:
            if j["core"]!=core or time.time()+len(opts)*20>=deadline:
                continue
            name=j["project"]
            taglist=j["tags"]
            assets=base.os.pathsep.join(f["path"] for f in pmap[name]["files"])
            subprocesses=[]
            states=[]
            for a in j["order"]:
                dst=where/name/a
                if not dst.exists():
                    if time.time()+15>=deadline:
                        break
                    # Global clock is still bounded. Each individual fresh JVM keeps the primary cap.
                    runner.measure(dst,j,"fixed",a,assets,options=opts[a],steps=4000,
                                   watchdog_s=max(1,deadline-time.time()-1))
                if not (dst/"status.json").is_file():
                    break
                obs=runner.attest(dst,j)
                rows=runner.metrics.rows_from(dst)
                states.append(obs)
                for m in obs["methods"]:
                    s=rows.get(m["tag"],{})
                    subprocesses.append(dict(m,arm=a,status=s.get("status","missing"),
                      analyze_ms=s.get("analyze_ms",0),wall_ms=s.get("wall_ms",0),
                      unknown=m["solver_unknown"],wall_timeout=m["exploration_timeouts"],
                      stats=s.get("stats",{})))
            if len(states)!=len(opts):
                continue
            rc=dict(start_msk=min(x["start"] for x in states),end_msk=max(x["end"] for x in states))
            res[j["id"]]=dict(records=subprocesses,state=rc,complete=not any(x["jvm_failed"] for x in states),
                                job=j,verified=all(x["verified"] for x in states))
        return res
    with ThreadPoolExecutor(4) as threads:
        acquired={k:v for g in threads.map(lane,base.CPUS) for k,v in g.items()}
    out=dict(results=acquired,schedule=schedule,end=base.now())
    base.write(result_path,out)
    return out

def main():
    base.setup_limits()
    global ROOT, COLD
    COLD="--cold" in sys.argv
    if COLD:
        ROOT=ROOT/"cold"
        ROOT.mkdir(exist_ok=True)
        g=cold_gate()
    else:
        g=gate()
    print("Live gate",base.now(),g["gate"],g["options"],flush=True)
    if not g["gate"] or "--gate-only" in sys.argv:
        return
    frozen_plan=frozen(g)
    assert time.time()+len(frozen_plan["arm_order"])*3*(DURATION+VALIDATION)+40*60<CUTOFF
    values=frozen_plan["values"]
    allowed=[i for i in range(1<<len(values)) if i.bit_count()<=2]
    raw=ROOT/"raw/live"
    raw.mkdir(parents=True,exist_ok=True)
    allowed=certify(raw/"certificates",allowed,values)
    if sum(i.bit_count()==1 for i in allowed)<2:
        print("Too few certified alternatives",base.now(),allowed,flush=True)
        return
    build=compile_driver("compact") if not COLD else None
    wins={}
    for rep,seq in enumerate(frozen_plan["arm_order"]):
        seed=frozen_plan["seed"][rep]
        for method in seq:
            where=raw/f"r{rep+1}-{method}"
            where.mkdir(exist_ok=True)
            arm_start=time.monotonic()
            baseline=prepare_pairs(where/"baseline",frozen_plan["splits"]["train"],{"A":{},"B":{}},
                                   seed,rep,build,"train-control",175 if COLD else ACQUIRE_CAP+5)
            aa=summarize(baseline,"B")
            aa_public={k:v for k,v in aa["control"].items()}
            def evaluate(dst,choice,available):
                result=prepare_pairs(dst,frozen_plan["splits"]["train"],{"A":{},"X":choice},
                          seed,rep,build,"train",min(available,175 if COLD else ACQUIRE_CAP+4))
                return summarize(result,"X")
            search_budget=DURATION-(time.monotonic()-arm_start)
            selected=optimizer.window(where,method,values,allowed,seed,search_budget,evaluate,aa_public,
                        165 if COLD else ACQUIRE_CAP+3,"Minimize check milliseconds on identical symbolic work subject to coverage and reliability.",max_sol=10)
            result=validate(where,frozen_plan["splits"]["validation"],
                    [mask for mask in selected["advanced"] if any(t.get("mask")==mask and t.get("result",{}).get("passes")
                     for t in selected["records"])],values,seed,rep,build)
            wins[where.name]=dict(options=optimizer.options(result["chosen"],values),search=selected,validation=result,
                                  training_budget=DURATION,baseline_s=DURATION-search_budget)
            print("chosen",where.name,result["chosen"],base.now(),flush=True)
    lock=ROOT/"evidence/live-test-lock.json"
    if not lock.exists():
        base.write(lock,dict(locked=base.now(),planned_sha=base.sha(ROOT/"inputs/live.json"),choices=wins))
    assert base.read(lock)["choices"]==wins
    vector_labels={(): "A"}
    arms={"A":{}}
    labels={}
    for key,rec in wins.items():
        opts=rec["options"]
        v=tuple(sorted(opts.items()))
        if v not in vector_labels:
            label=f"C{len(arms)}"
            vector_labels[v]=label
            arms[label]=opts
        labels[key]=vector_labels[v]
    tests=[]
    for rep in range(4):
        dst=raw/"test"/f"r{rep+1}"
        data=prepare_pairs(dst,frozen_plan["splits"]["test"],arms,20261124,rep,build,"test",610,trace=rep>=2)
        tests.append(data)
    values_by_arm={a:[summarize(d,a) for d in tests] for a in arms if a!="A"}
    combined={}
    for a,rr in values_by_arm.items():
        rows=list(chain.from_iterable(r["records"] for r in rr))
        combined[a]=dict(check=cluster_ratio(rows,"check_ms"),analyze=cluster_ratio(rows,"analyze_ms"),
                        par2=cluster_ratio(rows,"par2_ms"),paths=cluster_ratio(rows,"paths"))
    start=min(r["state"]["start_msk"] for d in tests for r in d["results"].values())
    end=max(r["state"]["end_msk"] for d in tests for r in d["results"].values())
    base.write(ROOT/"evidence/live-summary.json",dict(start=start,end=end,gate=g,locked=base.read(lock)["locked"],
                        labels=labels,configs=arms,arms=wins,test=values_by_arm,contrasts=combined,
                        test_jvms=sum(len(d["results"]) for d in tests)))

if __name__=="__main__":
    main()
