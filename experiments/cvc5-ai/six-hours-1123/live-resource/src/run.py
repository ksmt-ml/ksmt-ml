"""Calibrate a native resource bound before a disjoint live comparison."""
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
import os
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"stable/src"),str(TOP/"followup/src")]
from measure import base,measure,attest,metrics,ACQUISITION_END,FIXED_CAP,WALL_CAP
from analyze_power import compare
from recover import manifest,host_sample

LIMITS=(4000,16000,64000,256000,1024000)

def freeze():
    d=ROOT/"inputs/schedule.json"
    if d.exists():
        return base.read(d)
    old=base.read(TOP/"stable/inputs/schedule.json")
    heldout=base.read(TOP/"screening/inputs/research-split.json")
    cohorts={"calibration":old["projects"],"test":{p["project"]:p for p in
             [*heldout["splits"]["test"],*heldout["splits"]["validation"]]}}
    assert not cohorts["calibration"].keys() & cohorts["test"].keys()
    rng=random.Random(23111501)
    lists={}
    for group,projects in cohorts.items():
        sets=[]
        for i,(label,p) in enumerate(projects.items()):
            tags=[m["tag"] for m in p["methods"][:2]]
            order=([("fixed",a) for a in ("A","B",*[f"R{n}" for n in LIMITS])] if group=="calibration"
              else list(product(("wall","fixed"),("A","B","R","S"))))
            rng.shuffle(order)
            reps=2 if group=="calibration" else 4
            shifts=rng.sample(range(len(order)),k=reps)
            for r,c in enumerate(shifts):
                methods=tags[:]
                rng.shuffle(methods)
                sets.append(dict(id=f"{label}-r{r+1}",project=label,core=base.CPUS[(i+r)%4],
                   tags=methods,trace=(group=="calibration" or r>=2),seed=(0,71)[r%2],
                   order=order[c:]+order[:c]))
        rng.shuffle(sets)
        lists[group]=sets
    p=TOP/"protocols/15-live-resource.md"
    inputs=dict(at=base.now(),sha=base.sha(p),catalog=cohorts,assignments=lists,
          frozen_test_sha=base.sha(TOP/"screening/inputs/research-split.json"),
          stable_calibration_sha=base.sha(TOP/"stable/inputs/schedule.json"))
    base.write(d,inputs)
    return inputs

def acquire(design,group,options):
    raw=ROOT/"raw"/group
    record=ROOT/"evidence"/group
    raw.mkdir(exist_ok=True)
    record.mkdir(exist_ok=True)
    def lane(core):
        collected={}
        library=design["catalog"][group]
        for j in design["assignments"][group]:
            if j["core"]!=core:
                continue
            path=raw/j["id"]
            dst=record/f'{j["id"]}.json'
            if dst.exists():
                carry=base.read(dst)
                assert all(r["digest"]==manifest(path/a) for a,r in carry["arms"].items())
            else:
                requested=[f"{mode}-{a}" for mode,a in j["order"]]
                need=sum((FIXED_CAP if mode=="fixed" else WALL_CAP)+12 for mode,_ in j["order"])
                if time.time()+need>=ACQUISITION_END:
                    break
                if path.exists() and not all((path/a/"status.json").exists() for a in requested):
                    path.rename(raw/(j["id"]+f"-interrupted-{int(time.time())}"))
                p=library[j["project"]]
                assert all(base.sha(f["path"])==f["sha256"] for f in p["files"])
                jars=os.pathsep.join(f["path"] for f in p["files"])
                a={}
                for mode,label in j["order"]:
                    name=f"{mode}-{label}"
                    cell=path/name
                    if not cell.exists():
                        base.ensure_disk()
                        measure(cell,j,mode,label,jars,options=options[label])
                    a[name]=attest(cell,j)
                carry=dict(job=j,arms=a)
                base.write(dst,carry)
                print("live resource",group,j["id"],base.now(),sum(r["jvm_failed"] for r in a.values()),flush=True)
            collected[j["id"]]={k:(v,metrics.rows_from(path/k)) for k,v in carry["arms"].items()}
        return collected
    with ThreadPoolExecutor(4) as ex:
        return {k:v for lane in ex.map(lane,base.CPUS) for k,v in lane.items()}

def treatment(number):
    return {"reproducible-resource-limit":str(number)}

def summarize_calibration(jobs,results):
    rows={}
    for arm in ["B",*[f"R{x}" for x in LIMITS]]:
        observations=[results[j["id"]]["fixed-"+arm][0] for j in jobs]
        control=[results[j["id"]]["fixed-A"][0] for j in jobs]
        rows[arm]=metrics.comparison(observations,control)
        if len(jobs)!=10:
            rows[arm]["feasible"]=False
    return rows

def main():
    base.setup_limits()
    plan=freeze()
    assert plan["sha"]==base.sha(TOP/"protocols/15-live-resource.md")
    calibration={"A":{},"B":{}}
    calibration.update({f"R{x}":treatment(x) for x in LIMITS})
    pre=acquire(plan,"calibration",calibration)
    j=[r for r in plan["assignments"]["calibration"] if r["id"] in pre]
    if not j:
        return
    report=summarize_calibration(j,pre)
    eligible=[n for n in LIMITS if report[f"R{n}"]["complete"] and report[f"R{n}"]["feasible"]]
    actual=min(eligible) if eligible else None
    tested=actual or 64000
    lock=ROOT/"evidence/test-lock.json"
    if lock.exists():
        z=base.read(lock)
        assert (z["chosen"],z["diagnostic_tested"])==(actual,tested)
    else:
        base.write(lock,dict(locked=base.now(),chosen=actual,diagnostic_tested=tested,
             design=base.sha(ROOT/"inputs/schedule.json"),calibration=report,host=host_sample()))
    train_clocks=[q[0] for group in pre.values() for q in group.values()]
    base.write(ROOT/"evidence/calibration-summary.json",dict(start=min(x["start"] for x in train_clocks),
          end=max(x["end"] for x in train_clocks),jvms=len(train_clocks),blocks=len(j),planned=10,
          chosen=actual,locked=base.read(lock)["locked"],proof=report,host=host_sample()))
    if "--calibration-only" in sys.argv:
        return
    controls={"A":{},"B":{},"R":treatment(tested),"S":treatment(tested)}
    tests=acquire(plan,"test",controls)
    observed=[j for j in plan["assignments"]["test"] if j["id"] in tests]
    if not observed:
        return
    r={}
    for mode,trace,label in product(("wall","fixed"),(False,True),("B","R","S")):
        d={}
        for j in observed:
            value=tests[j["id"]]
            d[j["id"]]={k:v for k,v in value.items()}
            if label=="S":
                d[j["id"]][mode+"-A"]=value[mode+"-R"]
        name=f"{mode}-{label}-{'trace' if trace else 'plain'}"
        r[name]=compare(observed,mode,label,trace,d,estimate_sensitivity=False)
    test_clocks=[q[0] for group in tests.values() for q in group.values()]
    output=dict(start=min(x["start"] for x in train_clocks),
       test_start=min(x["start"] for x in test_clocks),end=max(x["end"] for x in test_clocks),
       lock=base.read(lock)["locked"],choice=actual,diagnostic_tested=tested,records=r,
       calibration=report,projects=sorted({x["project"] for x in observed}),blocks=len(observed),
       jvms=len(test_clocks),jvm_failures=sum(x["jvm_failed"] for x in test_clocks),host=host_sample())
    base.write(ROOT/"evidence/summary.json",output)

if __name__=="__main__":
    main()
