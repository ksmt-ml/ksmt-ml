"""Check the constant-array correction on fresh benign library methods."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import os
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"stable/src"),str(TOP/"followup/src")]
from measure import ACQUISITION_END, attest, base, measure, metrics
from analyze_power import compare
from recover import host_sample, manifest

PROTOCOL=TOP/"protocols/21-array-transfer.md"
ARMS={"A":{},"B":{},"E":{"arrays-exp":"true"},"z3":{}}
CAP=105


def freeze():
    path=ROOT/"inputs/design.json"
    if not path.is_file():
        src=TOP/"library-panel/src"
        spec=spec_from_file_location("array_static",src/"freeze.py")
        scanner=module_from_spec(spec)
        spec.loader.exec_module(scanner)
        initial=(src/"ScanPure.java").read_text()
        match="int size = 0, branches = 0, mult = 0, loops = 0;"
        assert initial.count(match)==1
        initial=initial.replace(match,match+"\n                        boolean allocation=false, read=false, write=false;")
        marker="                            size++;"
        assert initial.count(marker)==1
        initial=initial.replace(marker,marker+'''
                            allocation |= op == Opcodes.NEWARRAY;
                            read |= op >= Opcodes.IALOAD && op <= Opcodes.SALOAD;
                            write |= op >= Opcodes.IASTORE && op <= Opcodes.SASTORE;
''')
        marker="if (size < 8 || size > 240 || branches == 0) continue;"
        assert initial.count(marker)==1
        initial=initial.replace(marker,marker+"\n                        if (!allocation || !read || !write) continue;")
        (ROOT/"src/ScanPure.java").write_text(initial)
        scanner.ROOT=ROOT
        scanner.SEAL="23112101"
        extra=TOP/"library-panel/inputs/design.json"
        scope=scanner.history()
        if extra.is_file():
            scope.append((extra,extra.read_text()))
        scanner.history=lambda:scope
        scanner.COORDS={k:x for k,x in scanner.COORDS.items() if k not in ("commons-exec","log4j-api")}
        selected=scanner.static_selection(int(os.environ.get("SMT_SCAN_CPU","14")))
        rng=random.Random(23112102)
        jobs=[]
        for i,(p,row) in enumerate(selected["projects"].items()):
            order=list(ARMS)
            rng.shuffle(order)
            for rep,shift in enumerate(rng.sample(range(len(order)),3)):
                tags=[method["tag"] for method in row["methods"]]
                rng.shuffle(tags)
                jobs.append(dict(id=f"{p}-r{rep+1}",project=p,core=base.CPUS[(i+rep)%4],trace=True,
                        seed=(0,71)[(i+rep)%2],tags=tags,order=order[shift:]+order[:shift]))
        rng.shuffle(jobs)
        base.write(path,dict(at=base.now(),protocol=base.sha(PROTOCOL),jobs=jobs,
                             options=ARMS,projects=selected["projects"],static=base.sha(ROOT/"inputs/static.json")))
    data=base.read(path)
    assert data["protocol"]==base.sha(PROTOCOL) and data["static"]==base.sha(ROOT/"inputs/static.json")
    return data


def main():
    base.setup_limits()
    source=base.read(TOP/"array-tuning/evidence/gate.json")
    if not source["passed"]:
        return
    plan=freeze()
    if len(plan["projects"])<2 or "--freeze-only" in sys.argv:
        return
    def lane(core):
        results={}
        for j in plan["jobs"]:
            if j["core"]!=core:
                continue
            raw=ROOT/"raw"/j["id"]
            cert=ROOT/"evidence"/(j["id"]+".json")
            if cert.is_file():
                data=base.read(cert)
                assert all(v["digest"]==manifest(raw/a) for a,v in data["arms"].items())
            else:
                if time.time()+len(ARMS)*(CAP+5)>=ACQUISITION_END:
                    break
                if raw.exists() and not all((raw/a/"status.json").is_file() for a in ARMS):
                    raw.rename(raw.with_name(raw.name+f"-interrupted-{int(time.time())}"))
                record={}
                files=plan["projects"][j["project"]]["files"]
                assert all(base.sha(a["path"])==a["sha256"] for a in files)
                classpath=os.pathsep.join(x["path"] for x in files)
                for arm in j["order"]:
                    where=raw/arm
                    if not where.exists():
                        base.ensure_disk()
                        measure(where,j,"fixed",arm,classpath,options=ARMS[arm],watchdog_s=CAP)
                    record[arm]=attest(where,j)
                data=dict(job=j,arms=record)
                base.write(cert,data)
                print("library array",j["id"],base.now(),sum(r["jvm_failed"] for r in data["arms"].values()),flush=True)
            results[j["id"]]={"fixed-"+a:(r,metrics.rows_from(raw/a)) for a,r in data["arms"].items()}
        return results
    with ThreadPoolExecutor(4) as pool:
        records={k:v for group in pool.map(lane,base.CPUS) for k,v in group.items()}
    if not records:
        return
    jobs=[j for j in plan["jobs"] if j["id"] in records]
    results={a:compare(jobs,"fixed",a,True,records,estimate_sensitivity=False) for a in ARMS if a!="A"}
    table={a:Counter() for a in ARMS}
    for job in jobs:
        for arm in ARMS:
            for method in records[job["id"]]["fixed-"+arm][0]["methods"]:
                table[arm].update(method["unknown_reasons"])
    cells=[r for block in records.values() for r,_ in block.values()]
    base.write(ROOT/"evidence/summary.json",dict(start=min(r["start"] for r in cells),end=max(r["end"] for r in cells),
                jvms=len(cells),fails=sum(r["jvm_failed"] for r in cells),blocks=len(records),planned=len(plan["jobs"]),
                projects=sorted({j["project"] for j in jobs}),options=ARMS,contrasts=results,unknowns=table,host=host_sample()))


if __name__=="__main__":
    main()
