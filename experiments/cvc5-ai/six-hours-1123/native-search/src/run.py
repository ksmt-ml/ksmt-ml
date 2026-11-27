"""Use local JNI for all scored trials; transmit only the allowed train summary."""
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import os
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"recovery/scripts"))
sys.path.insert(0,str(TOP/"search/src"))
import base
import optimizer
from freeze import freeze
from recover import manifest,host_sample
from verify_live import percentile

CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()
TRAIN=540
VALIDATION=180
PROPOSER_RUNS=(("smac","sol","random"),("sol","random","smac"),("random","smac","sol"))
RELEASE=base.read(base.EXPECTED)
Z3=TOP.parent/"campaign/.venv/bin/z3"
CLASSES=TOP/"bv-study/build/classes"
USE_CPU=False

def decide():
    path=ROOT/"evidence/gate.json"
    a=TOP/"bv-study/evidence/summary.json"
    src=base.read(a)
    supported={}
    ordered={}
    for label,measurements in src["results"].items():
        candidates=[]
        for m in measurements:
            if m["family"] is None:
                continue
            totals=m["totals"]
            timing=m["ratios"]["cpu_known_ms"]
            if (m["programs"]>=4 and timing["value"] is not None and timing["value"]<=.95 and
                timing["ci"][1] is not None and timing["ci"][1]<=1 and totals["correct"][0]>=totals["correct"][1] and
                not totals["incorrect"][0] and totals["cpu_known_ms"][1]-totals["cpu_known_ms"][0]>=100):
                candidates.append(m["family"])
        config=src["option_sets"][label]
        if candidates and len(config)==1 and label not in src.get("observed_aliases",[]):
            supported.update(config)
        ordered[label]=dict(qualifying=candidates,config=config)
    res=dict(at=base.now(),source=base.sha(a),options=supported,heads=len(supported),evidence=ordered)
    if not path.exists():
        base.write(path,res)
    else:
        prior=base.read(path)
        assert prior["source"]==res["source"] and prior["options"]==res["options"]
    return res


def decide_interactions():
    evidence=TOP/"interactions/evidence/summary.json"
    digest=base.sha(evidence)
    d=base.read(evidence)
    selected={}
    valid=[]
    for label,rows in d["results"].items():
        opts=d["option_sets"][label]
        if not opts:
            continue
        aggregate=next(r for r in rows if r["family"] is None)
        t=aggregate["totals"]
        timing=aggregate["ratios"]["cpu_known_ms"]
        heads=(timing["value"] is not None and timing["value"]<=.95 and timing["ci"][1]<=1.0
              and t["cpu_known_ms"][1]-t["cpu_known_ms"][0]>=100 and not t["incorrect"][0]
              and all(z["totals"]["correct"][0]>=z["totals"]["correct"][1] for z in rows))
        if heads:
            valid.append(label)
            selected.update(opts)
    result=dict(at=base.now(),source=digest,heads=len(valid),qualifying=valid,options=selected)
    p=ROOT/"evidence/gate-interactions.json"
    if not p.exists():
        base.write(p,result)
    else:
        result=base.read(p)
        assert result["source"]==digest
    return result

def setup_oracle(split,where):
    where.mkdir(parents=True,exist_ok=True)
    def collect(p,i):
        out=where/p["id"]
        inp=ROOT/p["path"]
        assert base.sha(inp)==p["sha256"]
        if out.exists() and not (out/"status.json").is_file():
            out.rename(out.with_name(out.name+f"-interrupted-{int(time.time())}"))
        if not out.exists():
            out.mkdir()
            core=base.CPUS[i%4]
            cmd=["taskset","-c",str(core),str(Z3),"-T:18","-smt2",str(inp)]
            base.write(out/"invocation.json",dict(cmd=cmd,input=p["sha256"]))
            base.run_process(out,cmd,base.public_env(out),21)
        st=base.read(out/"status.json")
        got=(out/"stdout.log").read_text().splitlines()
        assert not any(a in ("sat","unsat") and a!=b for a,b in zip(got,p["expected"])),(p,got)
        return dict(program=p["id"],verified=got==p["expected"] and st["returncode"]==0,failure=st["watchdog"])
    def lane(i):
        return [collect(p,j) for j,p in enumerate(split) if j%4==i]
    with ThreadPoolExecutor(4) as ex:
        rows=[x for r in ex.map(lane,range(4)) for x in r]
    base.write(where/"verified.json",rows)
    confirmed={v["program"] for v in rows if v["verified"]}
    return [p for p in split if p["id"] in confirmed]

def run_case(path,program,core,opts,limit=15):
    src=ROOT/program["path"]
    if path.exists() and not (path/"status.json").is_file():
        path.rename(path.with_name(path.name+f"-interrupted-{int(time.time())}"))
    if not path.exists():
        path.mkdir(parents=True)
        cmd=["taskset","-c",str(core),*base.archive.jvm(path,"512m"),"-Dcvc5.skipLibraryLoad=true",
             "-Dcvc5jni="+str(base.archive.CVC5_JNI),"-cp",os.pathsep.join(map(str,(CLASSES,base.archive.CVC5_JAR))),
             "NativeSequence",str(src),*(f"{k}={v}" for k,v in opts.items())]
        base.write(path/"invocation.json",dict(cmd=cmd,opts=opts,core=core,source=program["sha256"]))
        base.run_process(path,cmd,base.public_env(path),limit)
    assert base.read(path/"invocation.json")["opts"]==opts and base.sha(src)==program["sha256"]
    st=base.read(path/"status.json")
    checks=[]
    before={}
    after={}
    ended=False
    for line in (path/"stdout.log").read_text().splitlines():
        p=line.split("\t")
        if p[0]=="RUNTIME":
            assert p[1]=="1.4.1" and p[3]=="1"
        elif p[0]=="OPTION":
            (before if p[1]=="before" else after)[p[2]]=p[3]
        elif p[0]=="CHECK":
            _,i,result,wall,cpu,reason=p
            if result.startswith("unknown (") and result.endswith(")"):
                result,reason="unknown",result[9:-1]
            i=int(i)
            assert i==len(checks)+1
            checks.append(dict(status=result,reason=reason,expected=program["expected"][i-1],
                           wall_ms=int(wall)/1e6,cpu_ms=int(cpu)/1e6))
        elif p[0]=="END":
            ended=int(p[1])==len(checks)==len(program["expected"])
    complete=ended and st["returncode"]==0 and not st["watchdog"]
    if complete:
        native={**opts,"incremental":"true","produce-models":"true","produce-unsat-cores":"true","seed":"0","tlimit-per":"1600"}
        assert all(before[x]==after[x]==v for x,v in native.items()), (path,before,after,native)
    while len(checks)<len(program["expected"]):
        checks.append(dict(status="missing",expected=program["expected"][len(checks)],reason="",wall_ms=0,cpu_ms=0))
    return dict(program=program["id"],family=program["family"],opts=opts,complete=complete,checks=checks,
                state=st,hashes=manifest(path))

def run_pairset(path,programs,options,scan_seed,deadline_s):
    path.mkdir(parents=True,exist_ok=True)
    output=path/"result.json"
    if output.exists():
        return base.read(output)
    rng=random.Random(scan_seed)
    picked=list(programs)
    rng.shuffle(picked)
    jobs=[]
    for n,p in enumerate(picked):
        arms=list(options)
        rng.shuffle(arms)
        jobs.append(dict(core=base.CPUS[n%4],id=p["id"],order=arms))
    base.write(path/"assignment.json",dict(at=base.now(),options=options,schedule=jobs))
    pmap={p["id"]:p for p in programs}
    wall_end=min(time.time()+deadline_s,CUTOFF)
    def lane(core):
        seen=[]
        for j in jobs:
            if j["core"]!=core:
                continue
            if time.time()+15*len(options)+2>=wall_end:
                continue
            r={}
            for a in j["order"]:
                r[a]=run_case(path/j["id"]/a,pmap[j["id"]],core,options[a])
            seen.append(dict(id=j["id"],arms=r))
        return seen
    with ThreadPoolExecutor(4) as pool:
        rows=[x for r in pool.map(lane,base.CPUS) for x in r]
    payload=dict(rows=rows,scheduled=len(jobs),end=base.now())
    base.write(output,payload)
    return payload

def score(payload,arm,reference="A"):
    def collect(name):
        seen=[]
        by=defaultdict(Counter)
        total=Counter()
        for pair in payload["rows"]:
            r=pair["arms"][name]
            fam=by[r["family"]]
            counts=Counter()
            for c in r["checks"]:
                counts["n"]+=1
                counts["checks"]+=1
                correct=c["status"]==c["expected"]
                counts["par2_ms"]+=c["wall_ms"] if correct else 3200
                counts["check_ms"]+=c["wall_ms"]
                counts["unknown"]+=c["status"]=="unknown"
                counts["solver_timeouts"]+=c["status"]=="unknown" and "TIMEOUT" in c["reason"].upper()
                counts["failed"]+=not correct
                counts["incorrect"]+=c["status"] in ("sat","unsat") and not correct
            counts["jvm_ms"]+=r["state"]["wall_ms"]
            fam.update(counts)
            total.update(counts)
            seen.append(r)
        return total,by,seen
    x,px,vx=collect(arm)
    y,py,vy=collect(reference)
    violations=[]
    for k in ("failed","incorrect","unknown","solver_timeouts"):
        if x[k]>y[k]:
            violations.append(k)
    if len(vx)!=payload["scheduled"] or any(not r["complete"] for r in [*vx,*vy]):
        violations.append("incomplete")
    ratio=x["par2_ms"]/y["par2_ms"] if y["par2_ms"] else 1.0
    cpu=[]
    for i,(a,b) in enumerate(zip(vx,vy,strict=True)):
        solved=[(m,n) for m,n in zip(a["checks"],b["checks"],strict=True) if m["status"]==n["status"]==m["expected"]]
        cpu.extend([(m["cpu_ms"],n["cpu_ms"]) for m,n in solved])
        if USE_CPU and any(m["status"]!=n["status"] for m,n in zip(a["checks"],b["checks"],strict=True)):
            violations.append(f"answer-sequence:{i}")
    u,v=map(sum,zip(*cpu)) if cpu else (0.0,0.0)
    cpu_ratio=u/v if v else 1.0
    primary=cpu_ratio if USE_CPU else ratio
    if USE_CPU and x["check_ms"]>1.02*y["check_ms"]:
        violations.append("check wall")
    def public(t,p):
        hidden={"incorrect"}
        return {**{k:v for k,v in t.items() if k not in hidden},
             "by_project":{f"P{j+1}":{k:v for k,v in p[key].items() if k not in hidden} for j,key in enumerate(sorted(p))}}
    train=public(x,px)
    control=public(y,py)
    train["cpu_ms"]=u
    control["cpu_ms"]=v
    return dict(feasible=not violations,passes=not violations and primary<=.95,
                cost=primary+(10+len(violations) if violations else 0),ratio=primary,par2_ratio=ratio,
                cpu_ratio=cpu_ratio,violations=violations,train=train,reference=control,totals=[x,y],families=[dict(px),dict(py)])

def certify(values,allowed,first):
    out=ROOT/"evidence/certified.json"
    if out.exists():
        return base.read(out)
    # The already frozen first train stream supplies an incremental check with the same API configuration.
    where=ROOT/"raw/certification"
    where.mkdir(parents=True,exist_ok=True)
    def lane(i):
        verified=[]
        for j,mask in enumerate(allowed):
            if j%4!=i:
                continue
            row=run_case(where/f"mask-{mask:02d}",first,base.CPUS[i],optimizer.options(mask,values))
            if row["complete"] and not any(c["status"] in ("sat","unsat") and c["status"]!=c["expected"] for c in row["checks"]):
                verified.append(mask)
        return verified
    with ThreadPoolExecutor(4) as x:
        good=sorted(m for rs in x.map(lane,range(4)) for m in rs)
    assert 0 in good
    base.write(out,good)
    return good

def trial_metrics(schedule,results,arm):
    if not schedule:
        return {}
    by_family=defaultdict(lambda:[0.0,0.0])
    for t in results:
        for r in t["rows"]:
            for j,a in enumerate((arm,"A")):
                n=r["arms"][a]
                by_family[n["family"]][j]+=sum(s["wall_ms"] if s["status"]==s["expected"] else 3200 for s in n["checks"])
    families=sorted(by_family)
    def ratio(xs):
        num=sum(by_family[k][0] for k in xs)
        den=sum(by_family[k][1] for k in xs)
        return num/den if den else None
    rng=random.Random(230911)
    vals=sorted(v for v in [ratio(rng.choices(families,k=len(families))) for _ in range(10000)] if v is not None)
    return dict(value=ratio(families),ci=[percentile(vals,p) for p in (.025,.975)],
                families={k:ratio([k]) for k in families})

def main():
    base.setup_limits()
    global USE_CPU
    USE_CPU="--from-interactions" in sys.argv
    g=decide_interactions() if USE_CPU else decide()
    print("native search gate",base.now(),g["heads"],g["options"],flush=True)
    if g["heads"]<2 or "--gate-only" in sys.argv:
        return
    d=freeze()
    assert d["protocol"]==base.sha(TOP/"protocols/09-native-search.md")
    assert base.sha(base.archive.CVC5_JNI)==RELEASE["cvc5_jni_sha256"]
    assert time.time()+9*(TRAIN+VALIDATION)+2700<CUTOFF
    training=setup_oracle(d["splits"]["train"],ROOT/"raw/oracle/train")
    vset=setup_oracle(d["splits"]["validation"],ROOT/"raw/oracle/validation")
    assert len(training)>=4 and len(vset)>=6
    values=g["options"]
    admitted=certify(values,[m for m in range(1<<len(values)) if m.bit_count()<=2],training[0])
    if sum(m.bit_count()==1 for m in admitted)<2:
        return
    started=base.now()
    choices={}
    for rep,order in enumerate(PROPOSER_RUNS):
        seed=231100+rep
        for algorithm in order:
            out=ROOT/"raw/search"/f"r{rep+1}-{algorithm}"
            baseline_clock=time.monotonic()
            bb=run_pairset(out/"baseline",training,{"A":{},"B":{}},seed,70)
            aa=score(bb,"B")
            def eval_live(dst,opts,left):
                return score(run_pairset(dst,training,{"A":{},"X":opts},seed,min(left,70)),"X")
            time_left=TRAIN-(time.monotonic()-baseline_clock)
            mission=("Minimize native thread CPU while preserving the full answer sequence and check wall."
                     if USE_CPU else "Minimize native check PAR2 with correct answers and no additional unknown results.")
            arm=optimizer.window(out,algorithm,values,admitted,seed,time_left,eval_live,aa["reference"],68,mission,max_sol=10)
            path=out/"validation.json"
            if path.exists():
                vv=base.read(path)
            else:
                start_valid=base.now()
                deadline=time.monotonic()+VALIDATION
                checked=[]
                for n,mask in enumerate(arm["advanced"]):
                    if deadline-time.monotonic()<75:
                        break
                    obs=run_pairset(out/"validation"/str(n+1),vset,{"A":{},"X":optimizer.options(mask,values)},seed+45,75)
                    checked.append(dict(mask=mask,result=score(obs,"X")))
                selected=min((r for r in checked if r["result"]["passes"]),default=None,key=lambda r:r["result"]["ratio"])
                vv=dict(chosen=selected["mask"] if selected else 0,start=start_valid,end=base.now(),checked=checked)
                base.write(path,vv)
            choices[out.name]=dict(arm=arm,validation=vv,options=optimizer.options(vv["chosen"],values))
            print("native selected",out.name,vv["chosen"],base.now(),flush=True)
    lock=ROOT/"evidence/test-lock.json"
    if not lock.exists():
        base.write(lock,dict(at=base.now(),source=base.sha(ROOT/"inputs/streams.json"),gate=base.sha(ROOT/"evidence"/("gate-interactions.json" if USE_CPU else "gate.json")),
                        choices=choices))
    assert base.read(lock)["choices"]==choices
    test=setup_oracle(d["splits"]["test"],ROOT/"raw/oracle/test")
    assert len(test)>=16 and len({p["family"] for p in test})>=3
    options={"A":{}}
    labels={}
    families={(): "A"}
    for name,rec in choices.items():
        selected=rec["options"]
        key=tuple(sorted(selected.items()))
        if key not in families:
            label=f"X{len(options)}"
            families[key]=label
            options[label]=selected
        labels[name]=families[key]
    scores=[]
    for rep in range(4):
        scores.append(run_pairset(ROOT/"raw/test"/f"r{rep+1}",test,options,231041+rep,900))
    reporter={arm:[score(r,arm) for r in scores] for arm in options if arm!="A"}
    base.write(ROOT/"evidence/summary.json",dict(start=started,end=base.now(),lock=base.read(lock)["at"],
             admissions=admitted,objective="cpu" if USE_CPU else "par2",n_test_programs=len(test),test_repeats=4,labels=labels,configs=options,choices=choices,
             performance=reporter,contrasts={k:trial_metrics(test,scores,k) for k in reporter},host=host_sample()))

if __name__=="__main__":
    main()
