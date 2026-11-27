"""Exploratory TRAIN-only matched option effects in the random-search prefix."""
from collections import defaultdict
from statistics import median

from common import ROOT, now, read, write


def compute(plan_path):
    plan=read(plan_path)
    by_bit={str(group["name"]):[] for group in plan["space"]}
    raw=ROOT/"raw"/plan["name"]
    history_seen=0
    for seed in plan["seeds"]:
        p=raw/f"s{seed}/random/trials.json"
        if not p.exists():
            continue
        history=read(p)
        eligible={r["id"]:r for r in history if r["valid"]}
        history_seen+=len(history)
        for bit,option in enumerate(plan["space"]):
            mask=1<<bit
            for a,left in eligible.items():
                b=a|mask
                if a!=b and b in eligible:
                    right=eligible[b]
                    by_bit[option["name"]].append(dict(seed=seed,off=a,on=b,off_cost=left["par2"],on_cost=right["par2"],
                                   delta=right["par2"]-left["par2"],ratio=right["par2"]/left["par2"],
                                   dsolved=right["solved"]-left["solved"]))
    results={}
    for label,samples in by_bit.items():
        changes=[q["delta"] for q in samples]
        results[label]=dict(pairs=len(samples),median_delta=median(changes) if changes else None,
                            median_ratio=median(q["ratio"] for q in samples) if samples else None,
                            improves=sum(q<0 for q in changes),worsens=sum(q>0 for q in changes),
                            raw=samples)
    path=ROOT/"evidence"/f"{plan['name']}-matched-train.json"
    write(path,dict(at=now(),decisions=history_seen,options=results,scope="exploratory TRAIN random-prefix matched masks"))
    return path


if __name__=="__main__":
    for path in (ROOT/"protocols").glob("*-search.json"):
        print(compute(path).relative_to(ROOT))
