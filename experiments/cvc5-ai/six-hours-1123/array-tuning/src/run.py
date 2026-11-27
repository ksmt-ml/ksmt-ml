"""Train-only Sol, real SMAC, and random with the same live Java-oracle budget."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
import random
import sys
import time

from design import ROOT,TOP,PROTOCOL,base,freeze

sys.path[:0]=[str(TOP/"search/src"),str(TOP/"followup/src")]
import optimizer
from analyze_power import cluster_ratio
from recover import manifest

TRAIN=270
VALIDATION=140
CAP=42
PAIR_RESERVE=2*(CAP+3)
CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()
VALUES={"arrays-exp":"true","arrays-eager-index":"false","static-learning":"false"}
ALLOWED=(0,1,2,3,4,5,6)
ORDER=(("smac","sol","random"),("sol","random","smac"),("random","smac","sol"))


def gate():
    parent=TOP/"array-semantics/evidence/summary.json"
    source=base.read(parent)
    results={}
    for a,opts in source["options"].items():
        if opts.get("arrays-exp")!="true":
            continue
        gains={k:rows[a]["complete"]-rows["A"]["complete"] for k,rows in source["families"].items()}
        issues=sum(rows[a]["wrong"]+rows[a]["witness_errors"]+rows[a]["jvm_failed"] for rows in source["families"].values())
        results[a]=dict(gains=gains,issues=issues,passed=(source["blocks"]==source["planned"] and not issues
                        and min(gains.values())>=0 and sum(gains.values())>=8 and sum(v>0 for v in gains.values())>=2))
    t=dict(at=base.now(),source=base.sha(parent),results=results,passed=sum(r["passed"] for r in results.values())>=2)
    dst=ROOT/"evidence/gate.json"
    if not dst.exists():
        base.write(dst,t)
    else:
        result=base.read(dst)
        assert {**result,"at":t["at"]}==t
        t=result
    return t


def setup(plan):
    spec=spec_from_file_location("tuning_kernel",TOP/"kernels/src/run.py")
    kernel=module_from_spec(spec)
    spec.loader.exec_module(kernel)
    kernel.ROOT=ROOT
    runtime=kernel.compile_fixture()
    module=kernel.reuse()
    selections={f:{n:r["tag"] for n,r in projects.items()} for part in plan["split"].values() for f,projects in part.items()}
    values={}
    for line in base.read(ROOT/"evidence/build/runtime.json")["concrete"].splitlines():
        family,cls,_,_,answers=line.split("\t")
        values[selections[family][cls]]=list(map(int,answers.split(",")))
    module.CASES=selections
    module.ROOT=ROOT
    outfile=ROOT/"inputs/oracle.json"
    d=dict(fixture=base.sha(runtime[0]),expected=values,design=base.sha(ROOT/"inputs/design.json"),cases=selections)
    if not outfile.exists():
        base.write(outfile,d)
    assert base.read(outfile)==d
    return kernel,module,runtime,values


def run_set(location, part, configs, seed, deadline, plan, engine, runtime, expected):
    location.mkdir(parents=True,exist_ok=True)
    record=location/"result.json"
    if record.exists():
        saved=base.read(record)
        assert saved["configs"]==configs and saved["split"]==part and saved["seed"]==seed
        return saved
    rng=random.Random(seed)
    families=sorted(plan["split"][part])
    rng.shuffle(families)
    jobs=[]
    for i,f in enumerate(families):
        tags=list(engine.CASES[f].values())
        order=list(configs)
        rng.shuffle(tags)
        rng.shuffle(order)
        jobs.append(dict(project=f,id=f,core=base.CPUS[(i+seed)%4],tags=tags,expected=[],seed=seed%2*71,order=order))
    base.write(location/"assignment.json",dict(configs=configs,split=part,seed=seed,jobs=jobs,at=base.now()))
    engine.ARMS=configs
    margin=len(configs)*(CAP+3)
    def one(job):
        if time.time()+margin>=min(deadline,CUTOFF):
            return None
        rows={}
        for arm in job["order"]:
            where=location/job["id"]/arm
            if where.exists() and not (where/"status.json").is_file():
                where.rename(where.with_name(where.name+f"-interrupted-{int(time.time())}"))
            if not where.exists():
                base.ensure_disk()
                engine.acquire(where,job,arm,runtime,watchdog_s=CAP)
            r=engine.audit(where,job)
            for cell in r["cells"].values():
                cell["expected"]=expected[cell["tag"]]
            rows[arm]=r
        return dict(job=job,arms=rows)
    with ThreadPoolExecutor(4) as pool:
        done=[r for r in pool.map(one,jobs) if r is not None]
    result=dict(at=base.now(),configs=configs,split=part,seed=seed,planned=len(jobs),rows=done)
    base.write(record,result)
    return result


def score(dataset, arm, control="A"):
    fields=("n","jvm_ms","checks","par2_ms","check_ms","steps","paths","coverage","failed","step_errors",
            "unknown","solver_timeouts","complete_outcomes","known_outcomes","retrieved_outcomes","store_all")
    totals={a:Counter({k:0 for k in (*fields,"wrong","edges","jvm_failed","witness_errors")}) for a in (arm,control)}
    projects={a:{} for a in totals}
    matched=[]
    for i,row in enumerate(sorted(dataset["rows"],key=lambda z:z["job"]["project"]),1):
        project=f"P{i}"
        for a in totals:
            values=row["arms"][a]
            t=Counter(jvm_ms=values["state"]["wall_ms"], jvm_failed=values["jvm_failed"])
            for r in values["cells"].values():
                t.update({k:r[k] for k in ("checks","par2_ms","check_ms","steps","paths","edges","coverage",
                        "failed","step_errors","solver_timeouts","wrong","witness_errors")})
                t.update(n=1,unknown=r["solver_unknown"],complete_outcomes=int(r["witnessed"]==r["expected"]),
                         known_outcomes=len(r["expected"]),retrieved_outcomes=len(r["witnessed"]),
                         store_all=sum(n for k,n in r["unknown_reasons"].items() if "STORE_ALL" in k))
            projects[a][project]=t
            totals[a].update(t)
        for method,x in row["arms"][arm]["cells"].items():
            y=row["arms"][control]["cells"][method]
            if (x["witnessed"]==y["witnessed"]==x["expected"] and not x["failed"] and not y["failed"] and
                all(x[k]==y[k] for k in ("steps","paths","edges","coverage","checks"))):
                matched.append(dict(project=project,check=[x["check_ms"],y["check_ms"]]))
    x,y=(totals[name] for name in (arm,control))
    restrictions=[]
    if len(dataset["rows"])!=dataset["planned"] or not x["n"]:
        restrictions.append("incomplete")
    for k in ("wrong","witness_errors","jvm_failed"):
        if x[k]:
            restrictions.append(k)
    for k in ("step_errors","unknown","solver_timeouts"):
        if x[k]>y[k]:
            restrictions.append(k)
    for k in ("steps","paths","edges"):
        if x[k]<.98*y[k]:
            restrictions.append(k)
    if x["coverage"]<y["coverage"]-x["n"]:
        restrictions.append("coverage")
    for p,z in projects[arm].items():
        prev=projects[control][p]
        if z["complete_outcomes"]<prev["complete_outcomes"] or z["paths"]<.95*prev["paths"]:
            restrictions.append(p)
    gain=x["complete_outcomes"]-y["complete_outcomes"]
    m=sum(r["check"][0] for r in matched)
    b=sum(r["check"][1] for r in matched)
    relative=m/b if b else None
    feasible=not restrictions
    gain_cost=1-(gain/max(1,x["n"]))+.01*min(2,relative or 1)-.01
    score_value=gain_cost+(10+len(restrictions) if restrictions else 0)
    accepted=feasible and (gain>=2 or (gain==0 and len(matched)==x["n"] and relative is not None and relative<=.95))
    def public(a):
        return {**{k:totals[a][k] for k in fields},
                "by_project":{p:{k:t[k] for k in fields} for p,t in projects[a].items()}}
    return dict(cost=score_value,feasible=feasible,passes=accepted,gain=gain,matched=len(matched),matched_check=relative,
                violations=restrictions,totals=[x,y],train=public(arm),reference=public(control),matching=matched)


def main():
    base.setup_limits()
    launch=base.now()
    admitted=gate()
    print("array search gate",admitted["passed"],launch,flush=True)
    if "--gate-only" in sys.argv or not admitted["passed"]:
        return
    assert time.time()+9*(TRAIN+VALIDATION)+1200<CUTOFF
    plan=freeze()
    assert plan["protocol"]==base.sha(PROTOCOL)
    for part in plan["split"].values():
        for programs in part.values():
            for cls,d in programs.items():
                assert d["sha"]==base.sha(ROOT/"src/programs"/(cls+".java"))
    kernel,engine,runtime,expected=setup(plan)
    kernel.ARMS={f"m{mask}":optimizer.options(mask,VALUES) for mask in ALLOWED}
    kernel.ARMS={"A":{},**{k:v for k,v in kernel.ARMS.items() if k!="m0"}}
    contracts=kernel.contracts()
    allowed=[mask for mask in ALLOWED if (mask==0 or contracts[f"m{mask}"]["ok"])]
    if not 1 in allowed or len(allowed)<4:
        return
    choices={}
    def measure(location,part,opts,seed,deadline):
        return run_set(location,part,opts,seed,deadline,plan,engine,runtime,expected)
    for i,order in enumerate(ORDER):
        for method in order:
            name=f"r{i+1}-{method}"
            where=ROOT/"raw/search"/name
            print("array search round",name,base.now(),flush=True)
            assignment=where/"budget.json"
            if not assignment.exists():
                where.mkdir(parents=True,exist_ok=True)
                base.write(assignment,dict(start=base.now(),end=time.time()+TRAIN))
            window=base.read(assignment)
            train_end=window["end"]
            seed=2311201+i
            baseline=score(measure(where/"baseline","train",{"A":{},"B":{}},seed,train_end),"B")
            def evaluate(out,opts,left):
                return score(measure(out,"train",{"A":{},"X":opts},seed,train_end),"X")
            search=optimizer.window(where,method,VALUES,allowed,seed,max(1,train_end-time.time()),evaluate,
                    baseline["reference"],PAIR_RESERVE,
                    "Increase complete Java outcome sets. Preserve steps, paths, coverage, and correct witnesses. Then minimize matched check time.",
                    max_sol=5,sol_limit=55)
            vout=where/"validation.json"
            if vout.is_file():
                validation=base.read(vout)
            else:
                at=base.now()
                end=time.time()+VALIDATION
                checked=[]
                for m in search["advanced"]:
                    if time.time()+PAIR_RESERVE>=end:
                        break
                    opts=optimizer.options(m,VALUES)
                    checked.append(dict(mask=m,result=score(measure(where/"validation"/f"m{m}","validation",
                                   {"A":{},"X":opts},seed+110,end),"X")))
                passed=sorted((x for x in checked if x["result"]["passes"]),key=lambda x:(x["result"]["cost"],x["mask"]))
                validation=dict(start=at,end=base.now(),checked=checked,chosen=passed[0]["mask"] if passed else 0)
                base.write(vout,validation)
            choices[name]=dict(train_start=window["start"],search=search,validation=validation,
                               options=optimizer.options(validation["chosen"],VALUES))
    lock=ROOT/"evidence/test-lock.json"
    if not lock.is_file():
        base.write(lock,dict(at=base.now(),design=base.sha(ROOT/"inputs/design.json"),choices=choices))
    assert base.read(lock)["choices"]==choices
    settings={"A":{},"E":optimizer.options(1,VALUES)}
    labels={}
    for k,choice in choices.items():
        exact=choice["options"]
        label=next((a for a,r in settings.items() if r==exact),None)
        if label is None:
            label=f"X{len(settings)-1}"
            settings[label]=exact
        labels[k]=label
    results=[]
    clocks=[]
    for rep in range(4):
        test=measure(ROOT/"raw/test"/f"r{rep+1}","test",settings,231201+rep,CUTOFF)
        results.append({name:score(test,name) for name in settings if name!="A"})
        clocks.extend(r["state"] for b in test["rows"] for r in b["arms"].values())
    ratios={a:cluster_ratio([row for r in results for row in r[a]["matching"]],"check") for a in settings if a!="A"}
    if clocks:
        base.write(ROOT/"evidence/summary.json",dict(start=min(c["train_start"] for c in choices.values()),
            end=max(c["end_msk"] for c in clocks), lock=base.read(lock)["at"],
            test_start=min(c["start_msk"] for c in clocks),options=settings,labels=labels,choices=choices,
            allowed=allowed,families=len(plan["split"]["test"]),test_jvms=len(clocks),test=results,ratios=ratios))


if __name__=="__main__":
    main()
