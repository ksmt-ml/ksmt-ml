"""Estimate between-project and repeat components from actual paired defaults."""
from collections import defaultdict
from math import ceil,log
from pathlib import Path
from statistics import mean,variance,NormalDist
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"recovery/scripts"))
import base

def components(cells,field):
    by=defaultdict(lambda:defaultdict(list))
    for r in cells:
        by[r["project"]][r["job"]].append(r[field])
    logs={}
    for p,runs in by.items():
        v=[]
        for pairs in runs.values():
            a,b=[sum(q[i] for q in pairs) for i in (0,1)]
            if a>0 and b>0:
                v.append(log(a/b))
        if len(v)>1:
            logs[p]=v
    if len(logs)<3:
        return {}
    reps=min(map(len,logs.values()))
    within=mean(variance(x) for x in logs.values())
    between=max(0.0,variance([mean(r) for r in logs.values()])-within/reps)
    actual=mean([mean(x) for x in logs.values()])
    z=NormalDist().inv_cdf(.95)+NormalDist().inv_cdf(.80)
    by_repeats={}
    for r in (1,2,4,8):
        # Normal approximation on the log of weighted project-level paired means.
        v=between+within/r
        by_repeats[str(r)]={str(pct):max(4,ceil(z*z*v/(log(1-pct/100)**2))) for pct in (2,5,10)}
    return dict(projects=len(logs),repeats=reps,log_stdev_within=within**.5,log_stdev_between=between**.5,
                mean_log_AA=actual,illustrative_projects=by_repeats,log_by_project=logs,
                assumptions="Gaussian independent project effects plus replicate effects; planning sensitivity on this sample")

def main():
    measurements=base.read(ROOT/"followup/evidence/power-summary.json")
    result={}
    for mode in ("wall","fixed"):
        obs=measurements["comparisons"][mode+"-B-plain"]["cells"]
        result[mode]={k:components(obs,k) for k in ("par2_ms","analyze_ms","check_ms")}
    base.write(ROOT/"diagnostics/evidence/repeat-precision.json",dict(at=base.now(),result=result))
    print({mode:{k:x.get("illustrative_projects",{}).get("2") for k,x in r.items()} for mode,r in result.items()})

if __name__=="__main__":
    main()
