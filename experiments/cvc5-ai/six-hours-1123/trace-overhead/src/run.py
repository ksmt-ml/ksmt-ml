"""Pair the same small methods with and without trace instrumentation."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"stable/src"),str(TOP/"followup/src")]
from measure import ACQUISITION_END,attest,base,measure,metrics
from analyze_power import STATS_EQUAL,cluster_ratio
from recover import manifest

PROTOCOL=TOP/"protocols/27-trace-overhead.md"
CAP=45


def freeze():
    schedule=ROOT/"inputs/schedule.json"
    witnessed=TOP/"kernels/inputs/schedule.json"
    jar=TOP/"kernels/build/kernels.jar"
    sealed=base.read(witnessed)
    if not schedule.is_file():
        rng=random.Random(23112701)
        jobs=[]
        for i,family in enumerate(("carry","division","packing","signed")):
            tags=sealed["cases"][family]
            sample=tags[min(tags)]
            arms=["A","B","T"]
            rng.shuffle(arms)
            for rep in (0,1):
                jobs.append(dict(id=family+f"-r{rep+1}",project=family,core=base.CPUS[(i+rep)%4],
                    tags=[sample],seed=(0,71)[rep],order=arms[rep:]+arms[:rep]))
        base.write(schedule,dict(at=base.now(),protocol=base.sha(PROTOCOL),source=base.sha(witnessed),
                                 jar_sha=sealed["fixture"],jobs=jobs))
    plan=base.read(schedule)
    assert plan["protocol"]==base.sha(PROTOCOL) and plan["source"]==base.sha(witnessed) and plan["jar_sha"]==base.sha(jar)
    return plan,jar


def main():
    base.setup_limits()
    plan,fixture=freeze()
    def lane(core):
        complete=[]
        for j in plan["jobs"]:
            if j["core"]!=core:
                continue
            path=ROOT/"raw"/j["id"]
            seal=ROOT/"evidence"/(j["id"]+".json")
            if seal.is_file():
                r=base.read(seal)
                assert all(manifest(path/a)==r["arms"][a]["digest"] for a in j["order"])
            else:
                if time.time()+3*(CAP+5)>=ACQUISITION_END:
                    break
                if path.exists() and not all((path/a/"status.json").is_file() for a in j["order"]):
                    path.rename(path.with_name(path.name+f"-interrupted-{int(time.time())}"))
                row={}
                for arm in j["order"]:
                    info={**j,"trace":arm=="T"}
                    cell=path/arm
                    if not cell.exists():
                        base.ensure_disk()
                        measure(cell,info,"fixed",arm,str(fixture),steps=3000,watchdog_s=CAP)
                    row[arm]=attest(cell,info)
                r=dict(job=j,arms=row)
                base.write(seal,r)
                print("trace overhead",j["id"],base.now(),flush=True)
            complete.append(r)
        return complete
    with ThreadPoolExecutor(4) as pool:
        blocks=[r for lane_result in pool.map(lane,base.CPUS) for r in lane_result]
    if not blocks:
        return
    observation={a:[] for a in ("B","T")}
    quality={a:Counter() for a in ("A","B","T")}
    clocks=[]
    for b in blocks:
        j=b["job"]
        tag=j["tags"][0]
        rows={a:metrics.rows_from(ROOT/"raw"/j["id"]/a).get(tag) for a in b["arms"]}
        for a,d in b["arms"].items():
            clocks.append(d)
            m=d["methods"][0]
            quality[a].update(jvm_failed=d["jvm_failed"],**{k:m[k] for k in ("paths","steps","coverage","checks","failed","solver_unknown","step_errors")})
        for a in observation:
            x,y=(rows[k] for k in (a,"A"))
            if (x and y and x["status"]==y["status"]=="ok" and all(x["stats"][k]==y["stats"][k] for k in STATS_EQUAL)
                    and x["stats"]["checks"] and not any(b["arms"][k]["methods"][0]["failed"] for k in (a,"A"))):
                observation[a].append(dict(project=j["project"],check=[x["stats"]["check_ms"],y["stats"]["check_ms"]],
                                           analyze=[x["analyze_ms"],y["analyze_ms"]]))
    base.write(ROOT/"evidence/summary.json",dict(start=min(r["start"] for r in clocks),end=max(r["end"] for r in clocks),
             jvms=len(clocks),blocks=len(blocks),planned=len(plan["jobs"]),quality=quality,
             matched={a:dict(n=len(rows),check=cluster_ratio(rows,"check"),analyze=cluster_ratio(rows,"analyze"))
                      for a,rows in observation.items()}))


if __name__=="__main__":
    main()
