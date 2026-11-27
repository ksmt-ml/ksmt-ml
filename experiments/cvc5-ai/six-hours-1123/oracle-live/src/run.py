"""Check concrete Java outcomes and live USVM witnesses on the same tiny programs."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import os
import random
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base
import metrics
from recover import manifest, host_sample

ARMS={"default":{},"bv-eager":{"bv-eager-eval":"true"},"no-simpl":{"simplification":"none"},"z3":{}}
CASES = {
    "ieee32": {"raw":"ieee/examples.ieee.Ieee32.fromFloatBits(F)I",
               "bv":"ieee/examples.ieee.Ieee32.fromBits(I)I","fp":"ieee/examples.ieee.Ieee32.direct(F)I"},
    "ieee64": {"raw":"ieee/examples.ieee.Ieee64.fromDoubleBits(D)I",
               "bv":"ieee/examples.ieee.Ieee64.fromBits(J)I","fp":"ieee/examples.ieee.Ieee64.direct(D)I"},
}
CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()

def prep():
    evidence=ROOT/"evidence/build"
    evidence.mkdir(exist_ok=True)
    marker=evidence/"runtime.json"
    build=ROOT/"build"
    fixture=build/"ieee.jar"
    out=build/"driver"
    trace=ROOT.parent/"followup/build/classes"
    if marker.exists():
        got=base.read(marker)
        assert manifest(out)==got["driver"] and base.sha(fixture)==got["jar"]
        return fixture,out,trace
    javaout=build/"java"
    scratch=build/"scratch"
    for p in (javaout,out,scratch):
        p.mkdir(exist_ok=True)
    stages={
        "java":["taskset","-c","14",str(base.archive.JDK/"javac"),"-J-XX:ActiveProcessorCount=1","-d",str(javaout),
                *map(str,sorted((ROOT/"src/classes").glob("*.java")))],
        "jar":["taskset","-c","14",str(base.archive.JDK/"jar"),"--create","--file",str(fixture),"-C",str(javaout),"."],
        "witness":["taskset","-c","14",*base.archive.jvm(scratch,"256m"),"-cp",str(fixture),"examples.ieee.WitnessControl"],
    }
    deps=("kotlin-compiler-embeddable-2.1.0.jar","kotlin-stdlib-2.1.0.jar","kotlin-script-runtime-2.1.0.jar",
          "kotlin-reflect-1.6.10.jar","kotlin-daemon-embeddable-2.1.0.jar","annotations-13.0.jar",
          "trove4j-1.0.20200330.jar","kotlinx-coroutines-core-jvm-1.6.4.jar")
    compiler=os.pathsep.join(str(next(base.archive.MODULES.rglob(n))) for n in deps)
    friends=[base.BUILD/"overlay",base.OLD/"build/client",trace]
    stages["driver"]=["taskset","-c","14",*base.archive.jvm(scratch,"1g"),"-cp",compiler,
         "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler","-no-stdlib","-no-reflect","-jvm-target","1.8",
         "-classpath",str(trace)+os.pathsep+base.RUNTIME,"-Xfriend-paths="+",".join(map(str,friends)),
         "-d",str(out),str(ROOT/"src/LiveOracle.kt")]
    for key,args in stages.items():
        root=evidence/key
        root.mkdir(exist_ok=True)
        if not (root/"status.json").is_file():
            base.write(root/"invocation.json",args)
            base.run_process(root,args,base.public_env(scratch),115)
        status=base.read(root/"status.json")
        assert status["returncode"]==0 and not status["watchdog"],(key,status,(root/"stderr.log").read_text())
    concrete=(evidence/"witness/stdout.log").read_text().splitlines()
    assert [int(p.split("\t")[1]) for p in concrete]==list(range(1,8))
    base.write(marker,dict(jar=base.sha(fixture),driver=manifest(out),concrete=concrete))
    return fixture,out,trace

def freeze(fixture):
    path=ROOT/"inputs/schedule.json"
    if path.exists():
        return base.read(path)
    rng=random.Random(231106)
    schedule=[]
    for i,(project,cases) in enumerate(CASES.items()):
        arms=list(ARMS)
        rng.shuffle(arms)
        shifts=list(range(4))
        rng.shuffle(shifts)
        for rep,k in enumerate(shifts):
            methods=list(cases.values())
            rng.shuffle(methods)
            schedule.append(dict(id=project+f"-r{rep+1}",project=project,core=base.CPUS[(i+rep)%4],
                                 seed=(0,71)[rep%2],tags=methods,expected=list(range(1,8)),
                                 order=arms[k:]+arms[:k]))
    rng.shuffle(schedule)
    data=dict(at=base.now(),protocol=base.sha(ROOT.parent/"protocols/06-live-oracle.md"),
              fixture=base.sha(fixture),arms=ARMS,schedule=schedule,cases=CASES)
    base.write(path,data)
    return data

def acquire(path,job,arm,built,extra_env=None,watchdog_s=105):
    path.mkdir(parents=True)
    scratch=path/"scratch"
    scratch.mkdir()
    jar,driver,trace=built
    active="z3" if arm=="z3" else "cvc5"
    env=base.public_env(scratch)
    env.update(USVM_CLIENT_STATS="1",LIVE141_ARM="z3" if arm=="z3" else "cvc5-default",USVM_SOLVER=active,
        USVM_TABLE_ITE="0",LIVE_SEARCH_OPTIONS=",".join(f"{k}={v}" for k,v in ARMS[arm].items()),
        SMT_ORACLE_FIXTURE=str(jar),SMT_TRACE="0",SMT_DELAY_US="0",
        **{"usvm.jvm.api.jar.path":base.archive.CP_ENV["APIJAR"],
          "usvm.jvm.approximations.jar.path":base.archive.CP_ENV["APPROXJAR"]})
    if extra_env:
        env.update(extra_env)
    cp=os.pathsep.join(map(str,(driver,trace,base.RUNTIME)))
    cmd=["taskset","-c",str(job["core"]),*base.archive.jvm(scratch,base.HEAP),
         "-Dcvc5.skipLibraryLoad=true",f"-Dlive141.cvc5.jni={base.archive.CVC5_JNI}",
         "-cp",cp,"org.usvm.dataset.LiveOracleKt",f"out={path}",f"fixture={base.OLD/'build/livefixture.jar'}",
         f"seed={job['seed']}",f"cp={jar}",f"methods={path/'methods.txt'}","budget=fixed","steps=3000"]
    (path/"methods.txt").write_text("\n".join(job["tags"])+"\n")
    base.write(path/"invocation.json",dict(cmd=cmd,env={k:env[k] for k in ("LIVE141_ARM","USVM_CLIENT_STATS",
          "LIVE_SEARCH_OPTIONS","USVM_SOLVER","SMT_ORACLE_FIXTURE",*(extra_env or {}))},
          arm=arm,core=job["core"],config=ARMS[arm]))
    try:
        return base.run_process(path,cmd,env,watchdog_s)
    finally:
        shutil.rmtree(scratch)

def audit(path,job):
    result=metrics.observe_block(path,job)
    log=metrics.rows_from(path)
    assert list(log)==job["tags"][:len(log)]
    state=base.read(path/"status.json")
    tags={tag:k for k,tag in CASES[job["project"]].items()}
    cells={}
    for summary in result["methods"]:
        tag=summary["tag"]
        witness=log.get(tag,{}).get("witnesses",[])
        categories=sorted({w["expected"] for w in witness if "expected" in w})
        valid_categories=sorted({w["actual"] for w in witness if w.get("valid")})
        cells[tags[tag]]=dict(summary,expected=job["expected"],observed=categories,
            witnessed=valid_categories,wrong=sum(w.get("valid") is False for w in witness),
            witness_errors=sum("error" in w for w in witness),witnesses=len(witness),
            errors=dict(Counter(w["error"] for w in witness if "error" in w)),
            truncated=log.get(tag,{}).get("states",0)>len(witness) and len(witness)==64)
    return dict(state=state,verified=result["verified"],jvm_failed=result["jvm_failed"],cells=cells,files=manifest(path))

def require_preregistered(expected):
    return expected==base.sha(ROOT.parent/"protocols/06-live-oracle.md")

def main():
    base.setup_limits()
    build=prep()
    plan=freeze(build[0])
    assert require_preregistered(plan["protocol"]) and plan["fixture"]==base.sha(build[0])
    raw=ROOT/"raw"
    evidence=ROOT/"evidence"
    def lane(core):
        observed=[]
        for j in plan["schedule"]:
            if j["core"]!=core or time.time()+len(j["order"])*115>=CUTOFF:
                continue
            where=raw/j["id"]
            if where.exists() and not all((where/a/"status.json").exists() for a in j["order"]):
                where.rename(raw/f"{j['id']}-interrupted-{int(time.time())}")
            data={}
            fresh=not where.exists()
            if fresh:
                base.ensure_disk()
            for a in j["order"]:
                if fresh:
                    acquire(where/a,j,a,build)
                data[a]=audit(where/a,j)
            output=evidence/f'{j["id"]}.json'
            result=dict(job=j,runs=data)
            if output.exists():
                assert base.read(output)==result
            else:
                base.write(output,result)
            observed.append(result)
            print("oracle live",j["id"],base.now(),sum(r["jvm_failed"] for r in data.values()),flush=True)
        return observed
    with ThreadPoolExecutor(4) as pool:
        obs=[r for rs in pool.map(lane,base.CPUS) for r in rs]
    summaries=defaultdict(Counter)
    clocks=[]
    for j in obs:
        project=j["job"]["project"]
        for arm,r in j["runs"].items():
            clocks.append(r["state"])
            for kind,q in r["cells"].items():
                key="|".join((project,arm,kind))
                ctr=summaries[key]
                ctr.update({k:q[k] for k in ("wrong","witness_errors","step_errors","solver_timeouts","other_unknown",
                                              "paths","edges","checks","steps","coverage","failed","witnesses")})
                ctr.update(dict(jvms=1,valid_all=int(q["witnessed"]==q["expected"]),
                                categories=len(q["witnessed"]),jvm_failed=r["jvm_failed"]))
    base.write(evidence/"summary.json",dict(start=min((c["start_msk"] for c in clocks),default=base.now()),
         end=max((c["end_msk"] for c in clocks),default=base.now()),host=host_sample(),
         arms=ARMS,study=bool(obs),blocks=len(obs),jvms=len(clocks),summary=summaries))

if __name__=="__main__":
    main()
