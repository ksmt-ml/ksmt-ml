"""Train-only exploratory screen after the frozen A/A measurement."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"followup/src"))
from analyze_power import STATS_EQUAL, cluster_ratio
from warm import ROOT, base, CUTOFF, compile_driver, observe, run_batch
from recover import host_sample,manifest

ARMS={
    "A":("FORK_DEPTH",{}),
    "B":("FORK_DEPTH",{}),
    "no-simpl":("FORK_DEPTH",{"simplification":"none"}),
    "bv-eager":("FORK_DEPTH",{"bv-eager-eval":"true"}),
    "array-index":("FORK_DEPTH",{"arrays-eager-index":"false"}),
    "bv-bool":("FORK_DEPTH",{"bv-to-bool":"true"}),
    "no-static":("FORK_DEPTH",{"static-learning":"false"}),
    "dfs-default":("DFS",{}),
    "dfs-no-simpl":("DFS",{"simplification":"none"}),
}
FIELDS=("par2_ms","analyze_ms","wall_ms","check_ms","steps","paths","edges","coverage","unknown",
        "solver_timeouts","wall_timeout","failed","step_errors","checks","missing")

def job(jid,project,seed,core,trace,steps,budget,arm_specs,rows):
    return dict(id=jid,project=project,seed=seed,core=core,trace=trace,steps=steps,budget=budget,
           cells=[dict(id=f"{i+1:03d}-{a}",tag=tag,arm=a,selector=arm_specs[a][0],opts=arm_specs[a][1])
                  for i,(tag,a) in enumerate(rows)])

def freeze(split):
    path=ROOT/"inputs/warm.json"
    if path.exists():
        return base.read(path)
    rng=random.Random(230404141)
    jobs=[]
    for i,project in enumerate(split["train"]):
        ranks={}
        for m in project["methods"]:
            arms=list(ARMS)
            rng.shuffle(arms)
            ranks[m["tag"]]=arms
        for r in range(4):
            tags=list(ranks)
            rng.shuffle(tags)
            rows=[]
            for tag in tags:
                arms=ranks[tag]
                k=(r*2)%len(arms)
                rows.extend((tag,a) for a in arms[k:]+arms[:k])
            p=project["project"]
            jobs.append(job(f"{p}-warm-r{r+1}",p,(0,71)[r%2],base.CPUS[(i+r)%4],r>=2,1500,
                      "fixed",ARMS,rows))
    rng.shuffle(jobs)
    spec=dict(at=base.now(),protocol_sha=base.sha(ROOT.parent/"protocols/04-warm-screen.md"),
              split_sha=base.sha(ROOT/"inputs/split.json"),jobs=jobs,arms=ARMS)
    base.write(path,spec)
    return spec

def pair_summary(jobs,data):
    table={}
    for trace in (False,True):
        for arm in [a for a in ARMS if a!="A"]:
            ref="dfs-default" if arm=="dfs-no-simpl" else "A"
            rows=[]
            dropped=Counter()
            for j in jobs:
                if j["trace"]!=trace:
                    continue
                group=defaultdict(dict)
                for r in data[j["id"]]["records"]:
                    group[r["tag"]][r["arm"]]=r
                for tag, by in group.items():
                    a,b=by[arm],by[ref]
                    row={k:[a[k],b[k]] for k in FIELDS}
                    row.update(project=j["project"],tag=tag,job=j["id"])
                    actual,base_stats=a["stats"],b["stats"]
                    equivalent=(a["status"]==b["status"]=="ok" and
                               all(actual[k]==base_stats[k] for k in STATS_EQUAL))
                    row["equivalent"]=equivalent
                    row["clean"]=not(a["failed"] or b["failed"])
                    row["exact"]=bool(equivalent and trace and actual["work_sha256"]==base_stats["work_sha256"])
                    row["checks_prefix"]=0
                    if trace and actual and base_stats:
                        for ha,hb in zip(actual["check_prefix_sha256"],base_stats["check_prefix_sha256"]):
                            if ha!=hb:
                                break
                            row["checks_prefix"]+=1
                    rows.append(row)
            stable=[r for r in rows if r["clean"] and r["equivalent"] and r["checks"][0]>0 and (r["exact"] or not trace)]
            summary={
                "n":len(rows),"equivalent":sum(r["equivalent"] for r in rows),"exact":sum(r["exact"] for r in rows),
                "shared_checks":sum(r["checks_prefix"] for r in rows),"common_check_denominator":sum(min(r["checks"]) for r in rows),
                "stable":len(stable),"stable_projects":sorted({r["project"] for r in stable}),
                "sum":{k:[sum(r[k][i] for r in rows) for i in (0,1)] for k in FIELDS},
                "ratios":{k:cluster_ratio(rows,k) for k in ("par2_ms","analyze_ms","check_ms","steps","paths","edges")},
                "stable_ratios":{k:cluster_ratio(stable,k) for k in ("analyze_ms","check_ms","wall_ms")},
                "stable_smt_share":sum(r["check_ms"][1] for r in stable)/max(1e-12,sum(r["analyze_ms"][1] for r in stable)),
                "stable_cells":[[r["project"],r["tag"],r["job"],r["checks"][0]] for r in stable],
            }
            table[f"{arm}-{'trace' if trace else 'plain'}"]=summary
    return table

def main():
    base.setup_limits()
    split=base.read(ROOT/"inputs/split.json")["splits"]
    projects={p["project"]:p for p in split["train"]}
    assert len(projects)>=2
    design=freeze(split)
    assert design["protocol_sha"]==base.sha(ROOT.parent/"protocols/04-warm-screen.md")
    raw=ROOT/"raw/warm"
    evidence=ROOT/"evidence/warm"
    raw.mkdir(exist_ok=True)
    evidence.mkdir(exist_ok=True)
    if not (evidence/"launch.json").exists():
        base.write(evidence/"launch.json",dict(at=base.now(),host=host_sample(),design_sha=base.sha(ROOT/"inputs/warm.json")))
    compiled=compile_driver()
    def lane(core):
        finished={}
        for j in design["jobs"]:
            if j["core"]!=core or time.time()+155>=CUTOFF:
                continue
            where=raw/j["id"]
            if where.exists() and not (where/"status.json").is_file():
                where.rename(raw/(j["id"]+f"-interrupted-{int(time.time())}"))
            if not where.exists():
                base.ensure_disk()
                run_batch(where,j,projects[j["project"]],compiled)
            r=observe(where,j)
            dest=evidence/f"{j['id']}.json"
            if dest.exists():
                assert base.read(dest)==r
            else:
                base.write(dest,r)
            print("warm",j["id"],base.now(),r["complete"],flush=True)
            finished[j["id"]]=r
        return finished
    with ThreadPoolExecutor(4) as pool:
        result={k:v for batch in pool.map(lane,base.CPUS) for k,v in batch.items()}
    jobs=[j for j in design["jobs"] if j["id"] in result]
    start=min((r["state"]["start_msk"] for r in result.values()),default=base.now())
    end=max((r["state"]["end_msk"] for r in result.values()),default=base.now())
    theory=defaultdict(Counter)
    for j in jobs:
        if j["trace"]:
            for row in result[j["id"]]["records"]:
                if row["arm"]=="A":
                    theory[j["project"]].update(row["stats"].get("theory_checks", {}))
    base.write(ROOT/"evidence/warm-summary.json",dict(start=start,end=end,jvms=len(jobs),
           projects=sorted({j["project"] for j in jobs}),errors=sum(not x["complete"] for x in result.values()),
           host=host_sample(),theories=theory,table=pair_summary(jobs,result)))

if __name__=="__main__":
    main()
