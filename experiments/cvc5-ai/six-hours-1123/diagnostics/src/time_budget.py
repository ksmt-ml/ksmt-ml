"""How much full-client time could a fixed-work check-only improvement change?"""
from collections import Counter,defaultdict
from pathlib import Path
import json
import math
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"recovery/scripts"))
import base

OPERATION_FIELDS=("internal_check_ms","translate_ms","assert_ms","check_ms","model_ms",
                  "type_solver_ms","decode_ms","push_ms","pop_ms","create_solver_ms")

def intake(paths):
    projects=defaultdict(Counter)
    for path in paths:
        st=base.read(path/"status.json")
        records=path/"results.jsonl"
        if st["returncode"]!=0 or st["watchdog"] or not records.is_file():
            continue
        rows=[json.loads(line) for line in records.read_text().splitlines()]
        if not rows:
            continue
        project=rows[0]["tag"].partition("/")[0]
        c=projects[project]
        c.update(jvms=1,jvm_ms=st["wall_ms"])
        for row in rows:
            s=row["stats"]
            c.update(n=1,analyze_ms=row["analyze_ms"],wall_ms=row["wall_ms"],cpu_ms=row["cpu_ms"],
                     checks=s["checks"],unknown=s["unknown"],
                     **{k:s.get(k,0) for k in OPERATION_FIELDS})
    def share(x):
        clean={**x}
        for field in ("analyze_ms","wall_ms","jvm_ms","internal_check_ms"):
            q=x[field]
            clean["check_share_"+field]=x["check_ms"]/q if q else None
        clean["max_gain_10pct_of_check_wall"]=(0.1*x["check_ms"]/x["wall_ms"]) if x["wall_ms"] else None
        return clean
    pooled=Counter()
    for q in projects.values():
        pooled.update(q)
    return dict(projects={p:share(x) for p,x in sorted(projects.items())},all=share(pooled))

def main():
    hist=ROOT/"recovery/raw/selection"
    follow=ROOT/"followup/raw/power"
    groups={}
    for a in ("smac","sol"):
        groups[f"original-train-{a}"]=list((hist/a/"train-default").glob("*/train"))
        groups[f"original-validation-{a}"]=list((hist/a/"validation").glob("*/default"))
    for mode in ("wall","fixed"):
        groups[f"new-{mode}-A"]=sorted(p for p in follow.glob("*/"+mode+"-A")
             if (p/"status.json").is_file() and base.read(p/"invocation.json")["env"]["SMT_TRACE"]=="0")
    pairs={k:intake(v) for k,v in groups.items()}
    base.write(ROOT/"diagnostics/evidence/time-budget.json",dict(at=base.now(),groups=pairs))
    for group,row in pairs.items():
        a=row["all"]
        print(group,a["n"],round(a["check_share_wall_ms"],3),round(a["check_share_jvm_ms"],3))

if __name__=="__main__":
    main()
