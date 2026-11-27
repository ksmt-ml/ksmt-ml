"""Use the wider set of independent projects to characterize detection and timing noise."""
from itertools import product
from pathlib import Path
from statistics import mean, stdev
import math
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"followup/src"),str(TOP/"recovery/scripts")]
import base
import metrics
from analyze_power import cluster_ratio

PROTOCOL=TOP/"protocols/12b-panel-precision.md"
PLANS=(.02,.05,.10)


def detect(rows,field):
    sums={}
    for r in rows:
        s=sums.setdefault(r["project"],[0.,0.])
        s[0]+=r[field][0]
        s[1]+=r[field][1]
    observed={p:math.log(x/y) for p,(x,y) in sums.items() if x>0 and y>0}
    n=len(observed)
    if n<5:
        return dict(projects=n)
    mid=mean(observed.values())
    values=[v-mid for v in observed.values()]
    critical=next(k for k in range(n+1) if sum(math.comb(n,j) for j in range(k,n+1))/2**n<=.05)
    rng=random.Random(23111212)
    draws=[rng.choices(values,k=n) for _ in range(10000)]
    return dict(projects=n,rejecting_signs=critical,a_a_geometric=math.exp(mid),sd_log=stdev(observed.values()),
           power={str(k):sum(sum(x < -math.log1p(-k) for x in s)>=critical for s in draws)/len(draws) for k in PLANS},
           a_a_project_log=observed)


def triple(a,p,traced):
    good=[]
    label="identical_trace" if traced else "aggregate_equivalent"
    pairs={(r["job"],r["tag"]):r for r in p["cells"]}
    for r in a["cells"]:
        z=pairs[r["job"],r["tag"]]
        if not (r["clean"] and z["clean"] and r[label] and z[label] and z["injected_ms"]>0):
            continue
        assert abs(r["check_ms"][1]-z["check_ms"][1])<1e-6
        delta=z["check_ms"][0]-mean(r["check_ms"])
        good.append(dict(project=r["project"],tag=r["tag"],job=r["job"],checks=z["checks"][0],
                    dose=[delta,z["injected_ms"]],time=[z["check_ms"][0],mean(r["check_ms"])]))
    return dict(n=len(good),dose=cluster_ratio(good,"dose"),time=cluster_ratio(good,"time"),data=good)


def candidate(panel):
    verified=[base.read(p) for p in sorted((ROOT/"evidence").glob("*-r*.json"))]
    modes={}
    for mode in ("fixed","wall"):
        a=[x["results"][mode+"-X"] for x in verified]
        b=[x["results"][mode+"-A"] for x in verified]
        if not a:
            continue
        totals=metrics.comparison(a,b)
        runs=[c for trace in ("trace","plain") for c in panel["contrasts"][f"{mode}-X-{trace}"]["cells"]]
        matched=[c for c in runs if c["identical_trace"] and c["clean"]]
        ratios={v:cluster_ratio(runs,v) for v in ("par2_ms","analyze_ms","check_ms","paths")}
        exact=cluster_ratio(matched,"check_ms")
        ci=ratios["par2_ms"]["ci"][1]
        passed=(ratios["par2_ms"]["clusters"]>=10 and totals["admissible"] and ci is not None and ci<1)
        modes[mode]=dict(passed=passed,comparison=totals,ratios=ratios,exact_count=len(matched),exact_check=exact)
    return modes


def main():
    path=ROOT/"evidence/summary.json"
    item=base.read(path)
    records={}
    for mode,trace in product(("fixed","wall"),("plain","trace")):
        name=mode+"-"+trace
        a=item["contrasts"][f"{mode}-B-{trace}"]
        p=item["contrasts"][f"{mode}-P-{trace}"]
        records[name]=dict(power={k:detect(a["cells"],k) for k in ("par2_ms","analyze_ms","check_ms")},
                           triple=triple(a,p,trace=="trace"))
    base.write(ROOT/"evidence/precision.json",dict(at=base.now(),protocol=base.sha(PROTOCOL),source=base.sha(path),
        results=records,candidate=candidate(item)))


if __name__=="__main__":
    main()
