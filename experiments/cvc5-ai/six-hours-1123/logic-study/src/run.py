"""Change exactly the declared logic of oracle-verified SMT-LIB programs."""
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from importlib.util import module_from_spec,spec_from_file_location
from pathlib import Path
import os
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
SOURCE=TOP/"native"
sys.path[:0]=[str(TOP/"followup/src"),str(TOP/"recovery/scripts")]
import base
from analyze_power import cluster_ratio
from recover import manifest

PROTOCOL=TOP/"protocols/25-logic.md"
CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()
OPTIONS={"tlimit-per":"1600","reproducible-resource-limit":"0"}
CAP=12


def freeze():
    seal=ROOT/"inputs/manifest.json"
    if not seal.is_file():
        source=SOURCE/"inputs/manifest.json"
        oracle=SOURCE/"evidence/oracle.json"
        confirmed={p["id"] for p in base.read(oracle) if p["verified"]}
        groups=defaultdict(list)
        for program in base.read(source)["programs"]:
            if program["id"] in confirmed:
                groups[program["family"]].append(program)
        rng=random.Random(23112501)
        programs={}
        jobs=[]
        for family in sorted(groups):
            for p in sorted(groups[family],key=lambda p:p["id"])[:2]:
                origin=SOURCE/p["path"]
                assert base.sha(origin)==p["sha256"]
                head,sep,tail=origin.read_bytes().partition(b"\n")
                assert head in (b"(set-logic QF_BV)",b"(set-logic QF_ABV)") and sep
                where=ROOT/"inputs/streams"/(p["id"]+".smt2")
                where.write_bytes(b"(set-logic ALL)\n"+tail)
                programs[p["id"]]={**p,"all":str(where.relative_to(ROOT)),"all_sha":base.sha(where),"logic":head.decode()}
                order=["A","B","C"]
                rng.shuffle(order)
                for rep in range(3):
                    jobs.append(dict(id=p["id"]+f"-r{rep+1}",program=p["id"],core=base.CPUS[(len(programs)+rep)%4],
                                      order=order[rep:]+order[:rep]))
        rng.shuffle(jobs)
        base.write(seal,dict(at=base.now(),protocol=base.sha(PROTOCOL),source=base.sha(source),oracle=base.sha(oracle),
                            programs=programs,jobs=jobs))
    d=base.read(seal)
    assert d["protocol"]==base.sha(PROTOCOL)
    return d


def main():
    base.setup_limits()
    plan=freeze()
    impl=spec_from_file_location("logic_budget",TOP/"budget/src/run.py")
    runner=module_from_spec(impl)
    impl.loader.exec_module(runner)
    classes=runner.build()

    def vm(where,arm,job,program):
        if arm=="U":
            source,digest,logic=ROOT/program["uf"],program["uf_sha"],program["uf_logic"]
        elif arm=="C":
            source,digest,logic=ROOT/program["all"],program["all_sha"],"ALL"
        else:
            source,digest,logic=SOURCE/program["path"],program["sha256"],program["logic"]
        assert base.sha(source)==digest
        if not where.exists():
            where.mkdir(parents=True)
            cmd=["taskset","-c",str(job["core"]),*base.archive.jvm(where,"512m"),"-Dcvc5.skipLibraryLoad=true",
                 "-Dcvc5jni="+str(base.archive.CVC5_JNI),"-cp",os.pathsep.join(map(str,(classes,base.archive.CVC5_JAR))),
                 "NativeSequence",str(source),*(k+"="+v for k,v in OPTIONS.items())]
            base.write(where/"invocation.json",dict(cmd=cmd,arm=arm,input_hash=digest,core=job["core"],
                       logic=logic))
            base.run_process(where,cmd,base.public_env(where),CAP)
        return runner.read_cell(where,program,OPTIONS)

    def lane(core):
        results=[]
        for job in plan["jobs"]:
            if job["core"]!=core:
                continue
            key=job["id"]
            where=ROOT/"raw"/key
            sealed=ROOT/"evidence"/(key+".json")
            if sealed.is_file():
                record=base.read(sealed)
                assert all(r["files"]==manifest(where/a) for a,r in record["results"].items())
            else:
                if time.time()+len(job["order"])*(CAP+2)>=CUTOFF:
                    break
                if where.exists() and not all((where/k/"status.json").is_file() for k in job["order"]):
                    where.rename(where.with_name(where.name+f"-interrupted-{int(time.time())}"))
                base.ensure_disk()
                p=plan["programs"][job["program"]]
                record=dict(job=job,results={a:vm(where/a,a,job,p) for a in job["order"]})
                base.write(sealed,record)
                print("logic",key,base.now(),flush=True)
            results.append(record)
        return results

    with ThreadPoolExecutor(4) as pool:
        blocks=[z for row in pool.map(lane,base.CPUS) for z in row]
    if not blocks:
        return
    time_rows=[r["st"] for b in blocks for r in b["results"].values()]
    arms=sorted({a for job in plan["jobs"] for a in job["order"]})
    groups={a:defaultdict(Counter) for a in arms}
    matched={a:[] for a in arms if a!="A"}
    for block in blocks:
        p=plan["programs"][block["job"]["program"]]
        family=p["family"]
        A=block["results"]["A"]["cells"]
        for a,r in block["results"].items():
            group=groups[a][family]
            group.update(jvms=1,jvm_failed=int(not r["complete"]))
            for x,y in zip(r["cells"],A,strict=True):
                correct=x["status"]==x["expected"]
                group.update(n=1,correct=correct,unknown=x["status"]=="unknown",missing=x["status"]=="missing",
                    incorrect=x["status"] in ("sat","unsat") and not correct,
                    timeout=x["status"]=="unknown" and "timeout" in x["reason"].lower())
                if a!="A" and correct and y["status"]==y["expected"]:
                    matched[a].append(dict(project=family,cpu=[x["cpu_ns"],y["cpu_ns"]],wall=[x["wall_ns"],y["wall_ns"]]))
    base.write(ROOT/"evidence/summary.json",dict(start=min(x["start_msk"] for x in time_rows),end=max(x["end_msk"] for x in time_rows),
               blocks=len(blocks),planned=len(plan["jobs"]),programs=len({d["job"]["program"] for d in blocks}),jvms=len(time_rows),
               outcomes=groups,options=OPTIONS,
               timing={a:dict(n=len(rows),**{key:cluster_ratio(rows,key) for key in ("cpu","wall")}) for a,rows in matched.items()}))


if __name__=="__main__":
    main()
