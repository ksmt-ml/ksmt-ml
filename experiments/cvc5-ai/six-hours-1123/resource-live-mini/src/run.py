"""Probe a single resource limit on short, exactly traced USVM methods."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"stable/src"),str(TOP/"followup/src")]
from measure import ACQUISITION_END,attest,base,measure,metrics
from analyze_power import compare
from recover import manifest

PROTOCOL=TOP/"protocols/26-resource-live-mini.md"
ARMS={"A":{},"B":{},"R":{"reproducible-resource-limit":"32000"}}
CAP=55
PRIORITY={"full-equal":0,"count-equal":1,"work-different":2}


def freeze():
    path=ROOT/"inputs/manifest.json"
    source=TOP/"trace-cause/inputs/schedule.json"
    if not path.is_file():
        base_set=base.read(source)["projects"]
        projects={}
        jobs=[]
        rng=random.Random(23112601)
        names=("automaton","lz4-java","commons-pool2","RoaringBitmap")
        for i, name in enumerate(names):
            p=base_set[name]
            tag=min(p["tags"],key=lambda t:(PRIORITY[p["tags"][t]],t))
            projects[name]=p["assets"]
            order=list(ARMS)
            rng.shuffle(order)
            for r in range(2):
                jobs.append(dict(id=f"{name}-r{r+1}",project=name,core=base.CPUS[(i+r)%4],
                                 tags=[tag],trace=True,seed=(0,71)[r],order=order[r:]+order[:r]))
        base.write(path,dict(at=base.now(),protocol=base.sha(PROTOCOL),source=base.sha(source),
                             projects=projects,jobs=jobs,options=ARMS))
    data=base.read(path)
    assert data["protocol"]==base.sha(PROTOCOL) and data["source"]==base.sha(source)
    return data


def main():
    base.setup_limits()
    plan=freeze()
    def lane(core):
        records={}
        for job in plan["jobs"]:
            if job["core"]!=core:
                continue
            run=ROOT/"raw"/job["id"]
            seal=ROOT/"evidence"/(job["id"]+".json")
            if seal.is_file():
                data=base.read(seal)
                assert all(manifest(run/a)==r["digest"] for a,r in data["arms"].items())
            else:
                if time.time()+len(ARMS)*(CAP+5)>=ACQUISITION_END:
                    break
                if run.exists() and not all((run/a/"status.json").exists() for a in ARMS):
                    run.rename(run.with_name(run.name+f"-interrupted-{int(time.time())}"))
                assets=plan["projects"][job["project"]]
                assert all(base.sha(x["path"])==x["sha256"] for x in assets)
                jars=os.pathsep.join(x["path"] for x in assets)
                observed={}
                for arm in job["order"]:
                    where=run/arm
                    if not where.exists():
                        base.ensure_disk()
                        measure(where,job,"fixed",arm,jars,options=ARMS[arm],steps=1500,watchdog_s=CAP)
                    observed[arm]=attest(where,job)
                data=dict(job=job,arms=observed)
                base.write(seal,data)
                print("short live resource",job["id"],base.now(),flush=True)
            records[job["id"]]={"fixed-"+a:(r,metrics.rows_from(run/a)) for a,r in data["arms"].items()}
        return records
    with ThreadPoolExecutor(4) as pool:
        all_rows={k:v for group in pool.map(lane,base.CPUS) for k,v in group.items()}
    if not all_rows:
        return
    jobs=[j for j in plan["jobs"] if j["id"] in all_rows]
    comparisons={k:compare(jobs,"fixed",k,True,all_rows,estimate_sensitivity=False) for k in ARMS if k!="A"}
    reasons={a:Counter() for a in ARMS}
    clock_rows=[]
    for j in jobs:
        for a in ARMS:
            r,_=all_rows[j["id"]]["fixed-"+a]
            clock_rows.append(r)
            for method in r["methods"]:
                reasons[a].update(method["unknown_reasons"])
    base.write(ROOT/"evidence/summary.json",dict(start=min(t["start"] for t in clock_rows),end=max(t["end"] for t in clock_rows),
            jvms=len(clock_rows),failures=sum(r["jvm_failed"] for r in clock_rows),
            planned=len(plan["jobs"]),blocks=len(jobs),projects=sorted({j["project"] for j in jobs}),
            comparisons=comparisons,options=ARMS,unknown_reasons=reasons))


if __name__=="__main__":
    main()
