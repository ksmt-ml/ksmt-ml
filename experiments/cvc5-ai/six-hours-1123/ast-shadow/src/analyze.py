"""Summarize verified typed-query outcomes by observed theory mix."""
from collections import Counter,defaultdict
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"followup/src"),str(TOP/"recovery/scripts")]
import base
from analyze_power import cluster_ratio


def main():
    source=ROOT/"evidence/summary.json"
    counts=defaultdict(Counter)
    matched=defaultdict(list)
    reasons=defaultdict(Counter)
    for receipt in sorted((ROOT/"evidence").glob("*-r*.json")):
        if not re.search(r"-r\d+\.json$",receipt.name):
            continue
        d=base.read(receipt)
        project=d["job"]["project"]
        for p in d["prefixes"]:
            for name,checks in p["answers"].items():
                for q in checks:
                    i=q["i"]-1
                    shape=p["shapes"][i]
                    theory=shape["theories"]+(" (cap)" if shape["truncated"] else "")
                    key=name+"|"+theory
                    status=q["status"]
                    counts[key].update([status])
                    if status=="UNKNOWN":
                        reasons[name].update([q["reason"]])
                    oracle=p["answers"].get("z3",[])
                    control=p["answers"].get("A",[])
                    if i>=len(oracle) or i>=len(control) or status not in ("SAT","UNSAT"):
                        continue
                    if name not in ("A","z3") and status==oracle[i]["status"]==control[i]["status"]:
                        matched[key].append(dict(project=project,cpu=[q["cpu_ms"],control[i]["cpu_ms"]],
                                                  wall=[q["wall_ms"],control[i]["wall_ms"]]))
    base.write(ROOT/"evidence/theory-results.json",dict(at=base.now(),source=base.sha(source),
          counts=counts,reasons=reasons,timings={k:dict(n=len(rows),cpu=cluster_ratio(rows,"cpu"),
                  wall=cluster_ratio(rows,"wall")) for k,rows in matched.items()}))


if __name__=="__main__":
    main()
