"""Remove the secondary per-check wall guard on a short diagnostic native subset."""
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import os
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
PRIOR=TOP/"budget"
sys.path[:0]=[str(TOP/"followup/src"),str(TOP/"recovery/scripts")]
import base
from analyze_power import cluster_ratio
from recover import manifest

PROTOCOL=TOP/"protocols/24-pure-resource.md"
OPTIONS={"A":{"tlimit-per":"800","reproducible-resource-limit":"8000"},
         "B":{"tlimit-per":"0","reproducible-resource-limit":"8000"},
         "C":{"tlimit-per":"0","reproducible-resource-limit":"32000"}}
CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()
CAP=8


def freeze():
    target=ROOT/"inputs/manifest.json"
    if not target.is_file():
        prior=base.read(PRIOR/"inputs/manifest.json")
        oracle=base.read(PRIOR/"evidence/oracle.json")
        by_project=defaultdict(list)
        for p in (PRIOR/"evidence/test").glob("*.json"):
            row=base.read(p)
            by_project[row["job"]["program"]].extend(v["reason"] for v in row["results"]["R"]["cells"])
        families=defaultdict(list)
        for pid,p in prior["programs"].items():
            if p["split"]=="test" and oracle[pid]:
                reasons=by_project[pid]
                total=sum("TIMEOUT" in s.upper() for s in reasons)
                both=total>0 and any("RESOURCEOUT" in s.upper() for s in reasons)
                families[p["family"]].append((not both,-total,pid))
        opts=[pid for family in sorted(families) for _,__,pid in sorted(families[family])[:2]]
        programs={k:prior["programs"][k] for k in opts}
        rng=random.Random(23112401)
        jobs=[]
        for i,p in enumerate(programs):
            order=list(OPTIONS)
            rng.shuffle(order)
            for rep in range(3):
                jobs.append(dict(id=f"{p}-r{rep+1}",program=p,core=base.CPUS[(i+rep)%4],
                 rep=rep+1,order=order[rep:]+order[:rep]))
        rng.shuffle(jobs)
        base.write(target,dict(at=base.now(),protocol=base.sha(PROTOCOL),source=base.sha(PRIOR/"evidence/summary.json"),
                             programs=programs,jobs=jobs,options=OPTIONS))
    data=base.read(target)
    assert data["protocol"]==base.sha(PROTOCOL) and data["source"]==base.sha(PRIOR/"evidence/summary.json")
    return data


def main():
    base.setup_limits()
    plan=freeze()
    spec=spec_from_file_location("budget_native",PRIOR/"src/run.py")
    native=module_from_spec(spec)
    spec.loader.exec_module(native)
    executable=native.build()

    def acquire(case,program,arm,core):
        source=PRIOR/program["path"]
        assert base.sha(source)==program["sha256"]
        if not case.is_dir():
            case.mkdir(parents=True)
            cmd=["taskset","-c",str(core),*base.archive.jvm(case,"512m"),"-Dcvc5.skipLibraryLoad=true",
                 "-Dcvc5jni="+str(base.archive.CVC5_JNI),"-cp",os.pathsep.join(map(str,(executable,base.archive.CVC5_JAR))),
                 "NativeSequence",str(source),*(k+"="+v for k,v in OPTIONS[arm].items())]
            base.write(case/"invocation.json",dict(cmd=cmd,input_sha=program["sha256"],options=OPTIONS[arm],core=core,arm=arm))
            base.run_process(case,cmd,base.public_env(case),CAP)
        return native.read_cell(case,program,OPTIONS[arm])

    def lane(core):
        completed=[]
        for j in plan["jobs"]:
            if j["core"]!=core:
                continue
            path=ROOT/"raw"/j["id"]
            seal=ROOT/"evidence"/(j["id"]+".json")
            if seal.exists():
                d=base.read(seal)
                assert all(r["files"]==manifest(path/k) for k,r in d["results"].items())
            else:
                if time.time()+3*(CAP+2)>=CUTOFF:
                    break
                if path.exists() and not all((path/k/"status.json").is_file() for k in OPTIONS):
                    path.rename(path.with_name(path.name+f"-interrupted-{int(time.time())}"))
                base.ensure_disk()
                d=dict(job=j,results={k:acquire(path/k,plan["programs"][j["program"]],k,core) for k in j["order"]})
                base.write(seal,d)
                print("pure resource",j["id"],base.now(),flush=True)
            completed.append(d)
        return completed

    with ThreadPoolExecutor(4) as pool:
        records=[z for row in pool.map(lane,base.CPUS) for z in row]
    if not records:
        return
    cells={a:Counter() for a in OPTIONS}
    timing={a:[] for a in ("B","C")}
    reason_transitions=Counter()
    clocks=[]
    for d in records:
        job=d["job"]
        family=plan["programs"][job["program"]]["family"]
        for a,r in d["results"].items():
            clocks.append(r["st"])
            cells[a].update(jvms=1,jvm_failed=not r["complete"])
            for i,q in enumerate(r["cells"]):
                label=q["status"]
                reason=q["reason"].upper()
                correct=label==q["expected"]
                cells[a].update(n=1,correct=correct,incorrect=label in {"sat","unsat"} and not correct,
                    missing=label=="missing",timeout="TIMEOUT" in reason,resourceout="RESOURCEOUT" in reason,
                    other_unknown=label=="unknown" and "TIMEOUT" not in reason and "RESOURCEOUT" not in reason,
                    cpu_ns=q["cpu_ns"],wall_ns=q["wall_ns"])
                if a=="A":
                    continue
                baseline=d["results"]["A" if a=="B" else "B"]["cells"][i]
                if a=="B":
                    left=baseline["reason"] or baseline["status"]
                    right=q["reason"] or label
                    reason_transitions.update([f"{left} → {right}"])
                if q["status"]==baseline["status"]==q["expected"]:
                    timing[a].append(dict(project=family,cpu=[q["cpu_ns"],baseline["cpu_ns"]],
                                          wall=[q["wall_ns"],baseline["wall_ns"]]))
    base.write(ROOT/"evidence/summary.json",dict(start=min(r["start_msk"] for r in clocks),end=max(r["end_msk"] for r in clocks),
      blocks=len(records),planned=len(plan["jobs"]),programs=len({d["job"]["program"] for d in records}),jvms=len(clocks),
      cells=cells,options=OPTIONS,changes=reason_transitions,
      timing={a:{k:cluster_ratio(rows,k) for k in ("cpu","wall")} for a,rows in timing.items()}))


if __name__=="__main__":
    main()
