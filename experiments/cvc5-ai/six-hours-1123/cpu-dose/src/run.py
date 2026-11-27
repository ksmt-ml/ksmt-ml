"""Measure a known CPU dose on the frozen JVM work projection."""
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec,spec_from_file_location
from pathlib import Path
import os
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"stable/src"),str(TOP/"followup/src")]
from measure import base,measure,attest,metrics,ACQUISITION_END,FIXED_CAP
from analyze_power import STATS_EQUAL,cluster_ratio
from recover import manifest,host_sample

def setup(cpu=14):
    spec=spec_from_file_location("component_trace",TOP/"trace-cause/src/run.py")
    loader=module_from_spec(spec)
    spec.loader.exec_module(loader)
    loader.ROOT=ROOT
    return loader.compile_overlay(cpu)

def freeze():
    dest=ROOT/"inputs/schedule.json"
    if dest.exists():
        return base.read(dest)
    source=TOP/"kernels/inputs/schedule.json"
    k=base.read(source)
    fixture=TOP/"kernels/build/kernels.jar"
    clinical=base.read(TOP/"stable/inputs/schedule.json")
    order={}
    rng=random.Random(23111406)
    for label,cases in k["cases"].items():
        ids=sorted(cases)
        tags=[cases[z] for z in ids[:2]]
        order["kernels-"+label]=dict(tags=tags,files=[{"path":str(fixture),"sha256":base.sha(fixture)}])
    for project in ("lz4-java","RoaringBitmap","automaton"):
        p=clinical["projects"][project]
        tags=[m["tag"] for m in p["methods"]]
        order[project]=dict(tags=tags,files=p["files"])
    jobs=[]
    for i,(proj,assets) in enumerate(order.items()):
        modes=["A","B","P"]
        rng.shuffle(modes)
        for rep in range(4):
            s=rep%3
            tags=assets["tags"][:]
            rng.shuffle(tags)
            jobs.append(dict(id=proj+f"-r{rep+1}",project=proj,seed=(0,71)[rep%2],
                core=base.CPUS[(i+rep)%4],trace=True,tags=tags,order=modes[s:]+modes[:s]))
    rng.shuffle(jobs)
    d=dict(at=base.now(),schedule=jobs,projects=order,dose_cpu_us=2000,fixture_plan_sha=base.sha(source),
           protocols=base.sha(TOP/"protocols/14-cpu-dose.md"))
    base.write(dest,d)
    return d

def main():
    base.setup_limits()
    design=freeze()
    assert design["protocols"]==base.sha(TOP/"protocols/14-cpu-dose.md")
    build=setup()
    evidence=ROOT/"evidence"
    def lane(core):
        data=[]
        for j in design["schedule"]:
            if j["core"]!=core or time.time()+3*(FIXED_CAP+10)>=ACQUISITION_END:
                continue
            path=ROOT/"raw"/j["id"]
            location=evidence/f'{j["id"]}.json'
            if location.is_file():
                result=base.read(location)
                assert all(r["check"]["digest"]==manifest(path/a) for a,r in result["arms"].items())
                data.append(result)
                continue
            catalog=design["projects"][j["project"]]["files"]
            assert all(base.sha(r["path"])==r["sha256"] for r in catalog)
            cp=os.pathsep.join(k["path"] for k in catalog)
            if path.exists() and not all((path/a/"status.json").exists() for a in j["order"]):
                path.rename(path.with_name(path.name+f"-interrupted-{int(time.time())}"))
            options={}
            for arm in j["order"]:
                out=path/arm
                if not out.exists():
                    base.ensure_disk()
                    measure(out,j,"fixed",arm,cp,runtime=build,delay_us=2000)
                row=attest(out,j)
                raw=metrics.rows_from(out)
                for m in raw.values():
                    s=m["stats"]
                    assert 0<=s["injected_cpu_ms"]<=s["check_thread_cpu_ms"] and 0<=s["check_thread_cpu_ms"]
                    if arm=="P":
                        assert s["injected_cpu_ms"]>=2*s["checks"]-1e-7
                    else:
                        assert s["injected_cpu_ms"]==0
                options[arm]=dict(check=row,rows=raw)
            acquired=dict(job=j,arms=options)
            base.write(location,acquired)
            data.append(acquired)
            print("CPU dose",j["id"],base.now(),sum(q["check"]["jvm_failed"] for q in options.values()),flush=True)
        return data
    with ThreadPoolExecutor(4) as pool:
        states=[r for v in pool.map(lane,base.CPUS) for r in v]
    samples=[]
    aa=[]
    total=Counter()
    for z in states:
        j=z["job"]
        cells=z["arms"]
        for tag in j["tags"]:
            total["methods"]+=1
            observed=[cells[a]["rows"].get(tag) for a in ("A","B","P")]
            if not all(q and q["status"]=="ok" for q in observed):
                continue
            outcomes={a:next(r for r in d["check"]["methods"] if r["tag"]==tag)["failed"] for a,d in cells.items()}
            a,b,p=[q["stats"] for q in observed]
            def equal(v,w):
                return v["work_sha256"]==w["work_sha256"] and all(v[k]==w[k] for k in STATS_EQUAL)
            if not outcomes["A"] and not outcomes["B"] and a["checks"]>=5 and equal(a,b):
                total["AA_match"]+=1
                aa.append(dict(project=j["project"],wall=[b["check_ms"],a["check_ms"]],
                              cpu=[b["check_thread_cpu_ms"],a["check_thread_cpu_ms"]]))
                if not outcomes["P"] and equal(p,a):
                    total["P_match"]+=1
                    wt=(a["check_ms"]+b["check_ms"])/2
                    ct=(a["check_thread_cpu_ms"]+b["check_thread_cpu_ms"])/2
                    samples.append(dict(project=j["project"],tag=tag,job=j["id"],
                       dose_wall=[p["check_ms"]-wt,p["injected_ms"]],
                       dose_cpu=[p["check_thread_cpu_ms"]-ct,p["injected_cpu_ms"]],
                       all_wall=[p["check_ms"],wt],all_cpu=[p["check_thread_cpu_ms"],ct]))
    clocks=[v["check"] for state in states for v in state["arms"].values()]
    reliability={a:{**metrics.aggregate([r["arms"][a]["check"] for r in states])} for a in ("A","B","P")}
    base.write(evidence/"summary.json",dict(start=min((q["start"] for q in clocks),default=base.now()),
        end=max((q["end"] for q in clocks),default=base.now()),projects=sorted(design["projects"]),jvms=len(clocks),
        cases=total,measurements={k:cluster_ratio(samples,k) for k in ("dose_wall","dose_cpu","all_wall","all_cpu")},
        aa={k:cluster_ratio(aa,k) for k in ("wall","cpu")},reliability=reliability,common=samples,host=host_sample()))

if __name__=="__main__":
    main()
