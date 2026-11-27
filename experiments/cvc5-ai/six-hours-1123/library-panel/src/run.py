"""Run the fixed expanded real-library protocol and verify each completed block."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import itertools
import os
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"stable/src"),str(TOP/"followup/src")]
from freeze import freeze
from measure import base,metrics,measure,attest,ACQUISITION_END,FIXED_CAP,WALL_CAP
from analyze_power import compare
from recover import host_sample,manifest

def main():
    base.setup_limits()
    bundle=freeze()
    assert bundle["protocol"]==base.sha(TOP/"protocols/12-library-panel.md")
    assert bundle["static_sha"]==base.sha(ROOT/"inputs/static.json")
    p=bundle["projects"]
    for library in p.values():
        assert all(base.sha(f["path"])==f["sha256"] for f in library["files"])
    summary=ROOT/"evidence"
    raw=ROOT/"raw"
    if not (summary/"launch.json").exists():
        base.write(summary/"launch.json",dict(at=base.now(),host=host_sample(),
                 schedule=base.sha(ROOT/"inputs/design.json"),source=base.sha(Path(__file__))))
    def lane(core):
        output={}
        for job in bundle["jobs"]:
            if job["core"]!=core:
                continue
            loc=raw/job["id"]
            cert=summary/f'{job["id"]}.json'
            labels=[f"{mode}-{arm}" for mode,arm in job["order"]]
            if cert.exists():
                info=base.read(cert)
                assert all(info["results"][a]["digest"]==manifest(loc/a) for a in labels)
            else:
                if time.time()+4*(FIXED_CAP+WALL_CAP+20)>=ACQUISITION_END:
                    break
                if loc.exists() and not all((loc/key/"status.json").is_file() for key in labels):
                    loc.rename(loc.with_name(loc.name+f"-interrupted-{int(time.time())}"))
                classpath=os.pathsep.join(f["path"] for f in p[job["project"]]["files"])
                started=not loc.exists()
                if started:
                    base.ensure_disk()
                observed={}
                for mode,arm in job["order"]:
                    label=f"{mode}-{arm}"
                    out=loc/label
                    if started:
                        measure(out,job,mode,arm,classpath,options=bundle["options"][arm],delay_us=2000)
                    observed[label]=attest(out,job)
                info=dict(job=job,results=observed)
                base.write(cert,info)
                print("library panel",job["id"],base.now(),sum(r["jvm_failed"] for r in observed.values()),flush=True)
            output[job["id"]]={a:(r,metrics.rows_from(loc/a)) for a,r in info["results"].items()}
        return output
    with ThreadPoolExecutor(4) as ex:
        results={k:v for part in ex.map(lane,base.CPUS) for k,v in part.items()}
    jobs=[j for j in bundle["jobs"] if j["id"] in results]
    seen=[r for j in jobs for r,_ in results[j["id"]].values()]
    grouped={}
    for mode,arm,trace in itertools.product(("wall","fixed"),("B","P","X"),(False,True)):
        name=f"{mode}-{arm}-{'trace' if trace else 'plain'}"
        grouped[name]=compare(jobs,mode,arm,trace,results,estimate_sensitivity=False)
    base.write(summary/"summary.json",dict(start=min((r["start"] for r in seen),default=base.now()),
      end=max((r["end"] for r in seen),default=base.now()),projects=sorted({j["project"] for j in jobs}),
      planned=len(bundle["jobs"]),completed=len(jobs),jvms=len(seen),failed=sum(r["jvm_failed"] for r in seen),
      caps=Counter(r["watchdog"] for r in seen if r["watchdog"]),opts=bundle["options"],contrasts=grouped,
      by_project=Counter(j["project"] for j in jobs),files_sha=base.sha(ROOT/"inputs/design.json"),host=host_sample()))

if __name__=="__main__":
    main()
