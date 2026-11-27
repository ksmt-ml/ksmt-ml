"""Measure independent hashes for USVM paths and KSMT query inputs."""
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"stable/src"),str(TOP/"followup/src")]
from measure import base,measure,attest,metrics,ACQUISITION_END,FIXED_CAP
from recover import manifest,host_sample

def compile_overlay(cpu=14, extra_friends=()):
    out=ROOT/"build/classes"
    log=ROOT/"evidence/build"
    if (log/"record.json").exists():
        assert base.read(log/"record.json")==manifest(out)
        return out
    out.mkdir(exist_ok=True)
    log.mkdir(exist_ok=True)
    temp=ROOT/"build/tmp"
    temp.mkdir(exist_ok=True)
    names=("kotlin-compiler-embeddable-2.1.0.jar","kotlin-stdlib-2.1.0.jar","kotlin-script-runtime-2.1.0.jar",
           "kotlin-reflect-1.6.10.jar","kotlin-daemon-embeddable-2.1.0.jar","trove4j-1.0.20200330.jar",
           "kotlinx-coroutines-core-jvm-1.6.4.jar","annotations-13.0.jar")
    cc=os.pathsep.join(str(next(base.archive.MODULES.rglob(n))) for n in names)
    deps=TOP/"followup/build/classes"
    sources=sorted((ROOT/"src").glob("*.kt"))
    command=["taskset","-c",str(cpu),*base.archive.jvm(temp,"1g"),"-cp",cc,
       "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler","-no-stdlib","-no-reflect","-jvm-target","1.8",
       "-classpath",str(deps)+os.pathsep+base.RUNTIME,
       "-Xfriend-paths="+",".join(map(str,(deps,*extra_friends))),"-d",str(out),*map(str,sources)]
    base.write(log/"invocation.json",dict(cmd=command,sources={p.name:base.sha(p) for p in sources}))
    st=base.run_process(log,command,base.public_env(temp),100)
    assert st["returncode"]==0 and not st["watchdog"],(st,(log/"stderr.log").read_text())
    base.write(log/"record.json",manifest(out))
    return out

def freeze():
    outputs=ROOT/"inputs/schedule.json"
    if outputs.exists():
        return base.read(outputs)
    history=TOP/"followup/evidence/power-summary.json"
    old=base.read(history)["comparisons"]["fixed-B-trace"]["cells"]
    design=base.read(TOP/"followup/inputs/power.json")
    rows=defaultdict(lambda:defaultdict(list))
    for obs in old:
        category=("full-equal" if obs["identical_trace"] else "count-equal" if obs["aggregate_equivalent"]
                  else "work-different")
        if obs["clean"] and min(obs["checks"])>=5:
            rows[obs["project"]][category].append(obs)
    pmap={}
    jobs=[]
    rng=random.Random(231110141)
    for i,(project,items) in enumerate(sorted(rows.items())):
        found={}
        for category,candidates in items.items():
            pick=max(candidates,key=lambda z:(min(z["checks"]),z["tag"]))
            if pick["tag"] not in found:
                found[pick["tag"]]=category
        pmap[project]=dict(assets=design["projects"][project]["files"],tags=found)
        ref=["A","B","dfs"]
        rng.shuffle(ref)
        for rep in range(3):
            order=list(found)
            rng.shuffle(order)
            jobs.append(dict(id=f"{project}-r{rep+1}",project=project,seed=(0,71)[(i+rep)%2],
              core=base.CPUS[(i+rep)%4],tags=order,trace=True,order=ref[rep:]+ref[:rep]))
    rng.shuffle(jobs)
    dataset=dict(frozen=base.now(),source=base.sha(history),projects=pmap,assigned=jobs,
            protocol=base.sha(TOP/"protocols/10-trace-cause.md"))
    base.write(outputs,dataset)
    return dataset

def agree(a,b,k):
    return a[k]==b[k]

def prefix(a,b,field):
    total=0
    for x,y in zip(a[field],b[field]):
        if x!=y:
            return total
        total+=1
    return total

def compare(z,left,right):
    j=z["job"]
    la=z["arms"][left]
    ra=z["arms"][right]
    b=[Counter(),Counter()]
    for i,cap in enumerate((la,ra)):
        b[i].update(jvm_fail=cap["attestation"]["jvm_failed"])
    for tag in j["tags"]:
        values=[cap["rows"].get(tag) for cap in (la,ra)]
        stats=[]
        for i,(row,cap) in enumerate(zip(values,(la,ra))):
            q=next(m for m in cap["attestation"]["methods"] if m["tag"]==tag)
            b[i].update({k:q[k] for k in ("paths","edges","steps","checks","failed","step_errors","solver_timeouts","other_unknown","coverage")})
            if row and row["status"]=="ok":
                stats.append(row["stats"])
        record=dict(project=j["project"],tag=tag,paired=len(stats)==2)
        if record["paired"]:
            a,bv=stats
            for s,s_name in ((a,left),(bv,right)):
                assert len(s["path_prefix_sha256"])==min(512,s["trace_steps"]),s_name
                assert min(512,s["checks"])<=len(s["query_prefix_sha256"])<=512,s_name
            record.update(path=agree(a,bv,"path_sha256"),query=agree(a,bv,"query_sha256"),
               operations=agree(a,bv,"operations_sha256"),combined=agree(a,bv,"work_sha256"),
               path_prefix=prefix(a,bv,"path_prefix_sha256"),query_prefix=prefix(a,bv,"query_prefix_sha256"),
               check_prefix=prefix(a,bv,"check_prefix_sha256"),steps=[a["trace_steps"],bv["trace_steps"]],
               checks=[a["checks"],bv["checks"]],
               available_step_prefix=min(*(len(s["path_prefix_sha256"]) for s in (a,bv))),
               available_query_prefix=min(*(len(s["query_prefix_sha256"]) for s in (a,bv))))
        yield record

def main():
    base.setup_limits()
    dataset=freeze()
    assert dataset["protocol"]==base.sha(TOP/"protocols/10-trace-cause.md")
    compiled=compile_overlay()
    found=ROOT/"evidence"
    def lane(core):
        measured=[]
        for job in dataset["assigned"]:
            if job["core"]!=core or time.time()+3*(FIXED_CAP+10)>=ACQUISITION_END:
                continue
            where=ROOT/"raw"/job["id"]
            known=found/f'{job["id"]}.json'
            if known.exists():
                d=base.read(known)
                assert all(v["attestation"]["digest"]==manifest(where/k) for k,v in d["arms"].items())
                measured.append(d)
                continue
            project=dataset["projects"][job["project"]]
            assert all(base.sha(a["path"])==a["sha256"] for a in project["assets"])
            jars=os.pathsep.join(p["path"] for p in project["assets"])
            if where.exists() and not all((where/a/"status.json").is_file() for a in job["order"]):
                where.rename(where.with_name(where.name+f"-interrupted-{int(time.time())}"))
            arms={}
            for arm in job["order"]:
                stage=where/arm
                if not stage.exists():
                    base.ensure_disk()
                    measure(stage,job,"fixed",arm,jars,selector="DFS" if arm=="dfs" else "FORK_DEPTH",runtime=compiled)
                arms[arm]=dict(attestation=attest(stage,job),rows=metrics.rows_from(stage))
            z=dict(job=job,arms=arms)
            base.write(known,z)
            measured.append(z)
            print("traces",job["id"],base.now(),flush=True)
        return measured
    with ThreadPoolExecutor(4) as workers:
        blocks=[j for g in workers.map(lane,base.CPUS) for j in g]
    clocks=[r["attestation"] for j in blocks for r in j["arms"].values()]
    out={}
    for a in ("B","dfs"):
        rows=[r for j in blocks for r in compare(j,a,"A")]
        p=[r for r in rows if r["paired"]]
        group=Counter(tuple(r[t] for t in ("path","query","operations")) for r in p)
        reliability=[]
        for arm in (a,"A"):
            runs=[j["arms"][arm]["attestation"] for j in blocks]
            reliability.append({**metrics.aggregate(runs)})
        out[a]=dict(planned=len(rows),paired=len(p),counts={" ".join(map(str,k)):v for k,v in group.items()},
              total_path_prefix=sum(r["path_prefix"] for r in p),total_query_prefix=sum(r["query_prefix"] for r in p),
              common_steps=sum(r["available_step_prefix"] for r in p),
              common_checks=sum(r["available_query_prefix"] for r in p),
              reliability=reliability,rows=rows)
    base.write(found/"summary.json",dict(start=min((r["start"] for r in clocks),default=base.now()),
       end=max((r["end"] for r in clocks),default=base.now()),jvms=len(clocks),projects=sorted(dataset["projects"]),
       blocks=len(blocks),comparisons=out,host=host_sample()))

if __name__=="__main__":
    main()
