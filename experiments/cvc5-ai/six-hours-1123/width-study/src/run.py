"""Verify the same mathematical workload at several widths and two native ceilings."""
from collections import defaultdict
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

from design import OPTIONS, PROTOCOL, ROOT, TOP, base, freeze

sys.path.insert(0,str(TOP/"interactions/src"))
import analyze as tools
from recover import manifest

spec=spec_from_file_location("width_native",TOP/"interactions/src/run.py")
native=module_from_spec(spec)
spec.loader.exec_module(native)


def analysis(plan):
    programs={p["id"]:p for p in plan["programs"]}
    oracles=base.read(ROOT/"evidence/oracle.json")
    observations=[]
    times=[]
    for f in sorted((ROOT/"evidence/pairs").glob("*.json")):
        block=base.read(f)
        job=block["job"]
        assert job in plan["jobs"]
        program=programs[job["program"]]
        for a,v in block["arms"].items():
            path=ROOT/"raw/paired"/job["id"]/a
            assert native.observe(path,program)==v and manifest(path)==v["digest"]
            times.append(v["state"])
        observations.append(block)
    if not observations:
        return
    results=[]
    for left,right in (("default-repeat","default"),("gauss","default"),("short-gauss","short")):
        rows=defaultdict(list)
        for block in observations:
            arms=block["arms"]
            if not (left in arms and right in arms):
                continue
            p=programs[block["job"]["program"]]
            for x,y in zip(tools.as_cells(arms[left],p["expected"]),tools.as_cells(arms[right],p["expected"]),strict=True):
                expected=x["expected"]
                assert y["expected"]==expected
                pair=(x,y)
                match=x["status"]==y["status"]==expected
                costs=[q["wall_ns"]/1e6 if q["status"]==expected else 2*int(OPTIONS[a].get("tlimit-per","1600"))
                       for a,q in zip((left,right),pair)]
                r=dict(family=p["family"],program=p["id"],statuses=[q["status"] for q in pair],
                       correct=[int(q["status"]==expected) for q in pair],match=match,
                       incorrect=[int(q["status"] in ("sat","unsat") and q["status"]!=expected) for q in pair],
                       timeout=[int(q["status"]=="unknown" and "timeout" in q["reason"].lower()) for q in pair],
                       solver_error=[int(q["status"].startswith("(error")) for q in pair],
                       par2=costs,cpu=[q["cpu_ns"]/1e6 if match else 0 for q in pair],
                       wall=[q["wall_ns"]/1e6 if match else 0 for q in pair])
                for scope in ("all",expected):
                    rows[p["width"],scope].append(r)
        for (width,scope),v in sorted(rows.items()):
            counts={k:[sum(r[k][i] for r in v) for i in (0,1)] for k in ("correct","incorrect","timeout","solver_error")}
            for k in ("unknown","missing"):
                counts[k]=[sum(r["statuses"][i]==k for r in v) for i in (0,1)]
            results.append(dict(arm=left,ref=right,width=width,expected=scope,n=len(v),
                matched=sum(r["match"] for r in v), counts=counts,
                ratios={k:tools.interval(v,k,"family") for k in ("cpu","wall","par2")}))
    base.write(ROOT/"evidence/summary.json",dict(start=min(t["start_msk"] for t in times),end=max(t["end_msk"] for t in times),
               jvms=len(times),jvm_failed=sum(bool(r["watchdog"] or r["returncode"]) for r in times),
               blocks=len(observations),planned=len(plan["jobs"]),options=OPTIONS,
               oracle={str(w):[sum(p["verified"] and programs[p["id"]]["width"]==w for p in oracles),
                               sum(p["width"]==w for p in programs.values())]
                       for w in sorted({p["width"] for p in programs.values()})},results=results))


def main():
    plan=freeze()
    native.ROOT=ROOT
    native.OPTIONS=OPTIONS
    native.main(PROTOCOL)
    analysis(plan)


if __name__=="__main__":
    main()
