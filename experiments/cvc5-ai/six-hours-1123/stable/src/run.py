"""Screen options only on the signed work projection from initial A/A."""
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import random
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"followup/src"))
from analyze_power import STATS_EQUAL,cluster_ratio
from measure import base,metrics,measure,attest,ACQUISITION_END,FIXED_CAP
from recover import host_sample,manifest

ARMS={
    "A":{},"B":{},"P":{},"bv-eager":{"bv-eager-eval":"true"},"bv-bool":{"bv-to-bool":"true"},
    "no-static":{"static-learning":"false"},"array-index":{"arrays-eager-index":"false"}
}
DOSE=2000

def freeze():
    path=ROOT/"inputs/schedule.json"
    if path.exists():
        return base.read(path)
    source=TOP/"followup/evidence/power-summary.json"
    old=base.read(source)
    upfront=base.read(TOP/"followup/inputs/power.json")
    priors={j["id"]:j for j in upfront["jobs"]}
    chosen=defaultdict(lambda: defaultdict(dict))
    for r in old["comparisons"]["fixed-B-trace"]["cells"]:
        if r["identical_trace"] and r["clean"] and r["checks"][0]>=5 and min(r["check_ms"])>=10:
            rec=chosen[r["project"]][r["tag"]]
            rec[str(priors[r["job"]]["seed"])]=min(r["check_ms"])
    workload={}
    jobs=[]
    rng=random.Random(23110814)
    for i,(name,tags) in enumerate(sorted(chosen.items())):
        p=upfront["projects"][name]
        tags=sorted(tags, key=lambda t:(-len(tags[t]),-sum(tags[t].values()),t))[:2]
        use=[m for m in p["methods"] if m["tag"] in tags]
        workload[name]=dict(files=p["files"],methods=use)
        labels=list(ARMS)
        rng.shuffle(labels)
        displacements=rng.sample(range(len(labels)),4)
        for rep,d in enumerate(displacements):
            tag_sequence=tags.copy()
            rng.shuffle(tag_sequence)
            jobs.append(dict(id=f"{name}-r{rep+1}",project=name,core=base.CPUS[(i+rep)%4],seed=(0,71)[rep%2],
                        tags=tag_sequence,trace=True,order=labels[d:]+labels[:d]))
    assert len(workload)>=4
    rng.shuffle(jobs)
    base.write(path,dict(at=base.now(),input_sha=base.sha(source),dose_us=DOSE,projects=workload,jobs=jobs,
               arms=ARMS,protocol=base.sha(TOP/"protocols/08b-cold-screen.md")))
    return base.read(path)

def compare(all_jobs,arm):
    noisy=[]
    stable=[]
    candidate=[]
    for item in all_jobs:
        j=item["job"]
        by=item["arms"]
        observations=by[arm]["check"]
        aa=by["A"]["check"]
        ab=by["B"]["check"]
        lookup={}
        for a,d in ((arm,observations),("A",aa),("B",ab)):
            lookup[a]={m["tag"]:m for m in d["methods"]}
        for tag in j["tags"]:
            x,y,z=[by[a]["rows"].get(tag) for a in (arm,"A","B")]
            cx,cy,cz=[lookup[a][tag] for a in (arm,"A","B")]
            clean=lambda q:q and q["status"]=="ok"
            def same(u,v):
                return bool(clean(u) and clean(v) and all(u["stats"][k]==v["stats"][k] for k in STATS_EQUAL)
                  and u["stats"]["work_sha256"]==v["stats"]["work_sha256"])
            row=dict(project=j["project"],tag=tag,job=j["id"],seed=j["seed"],same=same(x,y),
                     steps=[m["steps"] for m in (cx,cy)],paths=[m["paths"] for m in (cx,cy)],
                     checks=[m["checks"] for m in (cx,cy)],
                     failed=[m["failed"] for m in (cx,cy)],step_errors=[m["step_errors"] for m in (cx,cy)],
                     pen=[m["par2_ms"] for m in (cx,cy)],unknown=[m["solver_unknown"] for m in (cx,cy)],
                     timeout=[m["solver_timeouts"] for m in (cx,cy)],
                     coverage=[m["coverage"] for m in (cx,cy)],
                     check_ms=[m["check_ms"] for m in (cx,cy)],
                     analyze_ms=[r["analyze_ms"] if r else 0 for r in (x,y)])
            noisy.append(row)
            if not(y and z and by["A"]["check"]["jvm_failed"]==by["B"]["check"]["jvm_failed"]==0
                   and not cy["failed"] and not cz["failed"] and same(y,z) and cy["checks"]>=5):
                continue
            aa_ms=(cy["check_ms"]+cz["check_ms"])/2
            aa_time=(y["analyze_ms"]+z["analyze_ms"])/2
            rr={**row,"check_ms":[cx["check_ms"],aa_ms],"analyze_ms":[x["analyze_ms"] if x else 0,aa_time],
                       "control_delta":[cy["check_ms"],cz["check_ms"]]}
            if x:
                rr["dose_ratio"]=[cx["check_ms"]-aa_ms,x["stats"]["injected_ms"]]
            stable.append(rr)
            if not cx["failed"] and row["same"] and by[arm]["check"]["jvm_failed"]==0:
                candidate.append(rr)
    ratios={k:cluster_ratio(noisy,k) for k in ("pen","steps","paths","check_ms","analyze_ms")}
    matches={k:cluster_ratio(candidate,k) for k in ("check_ms","analyze_ms","control_delta")}
    if arm=="P":
        matches["dose"]=cluster_ratio([r for r in candidate if r.get("dose_ratio",[0,0])[1]>0],"dose_ratio")
    good=(len(stable)==len(candidate) and bool(stable) and matches["check_ms"]["value"] is not None and
          matches["check_ms"]["value"]<=.95 and matches["analyze_ms"]["value"]<=1.02 and
          sum(1 for x in matches["check_ms"]["projects"].values() if x is not None and x<=.95)>=2)
    return dict(ratios=ratios,matched=matches,cells=noisy,shared=stable,identical=len(candidate),
                n=len(noisy),aa_same=len(stable),accept=bool(good and arm not in ("B","P")),
                metrics={k:[sum(r[k][i] for r in noisy) for i in (0,1)] for k in
                        ("steps","paths","checks","coverage","failed","step_errors","unknown","timeout")})

def main():
    base.setup_limits()
    design=freeze()
    assert design["protocol"]==base.sha(TOP/"protocols/08b-cold-screen.md")
    project=design["projects"]
    for p in project.values():
        assert all(base.sha(a["path"])==a["sha256"] for a in p["files"])
    evidence=ROOT/"evidence"
    raw=ROOT/"raw"
    def lane(core):
        sets=[]
        for j in design["jobs"]:
            if j["core"]!=core or time.time()+len(ARMS)*(FIXED_CAP+10)>=ACQUISITION_END:
                continue
            dest=evidence/f'{j["id"]}.json'
            d=raw/j["id"]
            if dest.exists():
                results=base.read(dest)
                assert all(v["check"]["digest"]==manifest(d/a) for a,v in results["arms"].items())
                sets.append(results)
                continue
            if d.exists() and not all((d/a/"status.json").is_file() for a in j["order"]):
                d.rename(raw/f'{j["id"]}-interrupted-{int(time.time())}')
            base.ensure_disk()
            assets=os.pathsep.join(r["path"] for r in project[j["project"]]["files"])
            arms={}
            fresh=not d.exists()
            for a in j["order"]:
                where=d/a
                if fresh:
                    measure(where,j,"fixed",a,assets,options=ARMS[a],delay_us=DOSE)
                arms[a]=dict(check=attest(where,j),rows=metrics.rows_from(where))
            complete=dict(job=j,arms=arms)
            base.write(dest,complete)
            sets.append(complete)
            print("stable",j["id"],base.now(),sum(r["check"]["jvm_failed"] for r in arms.values()),flush=True)
        return sets
    with ThreadPoolExecutor(4) as pool:
        data=[row for r in pool.map(lane,base.CPUS) for row in r]
    observed=[d["check"] for j in data for d in j["arms"].values()]
    a={name:compare(data,name) for name in ARMS if name!="A"}
    result=dict(start=min((x["start"] for x in observed),default=base.now()),
        end=max((x["end"] for x in observed),default=base.now()),projects=sorted(project),blocks=len(data),jvms=len(observed),
        errors=sum(r["jvm_failed"] for r in observed),arms=ARMS,results=a,
        admission=[p for p,r in a.items() if r["accept"]],host=host_sample())
    base.write(evidence/"summary.json",result)

if __name__=="__main__":
    main()
