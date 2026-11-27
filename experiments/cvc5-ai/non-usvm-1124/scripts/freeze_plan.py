"""Freeze a concrete solver-search plan from already observed TRAIN only."""
from pathlib import Path
import argparse

from common import ROOT, now, read, sha, write
from eval import MENU, manifest


def make(producer, screen, protocol, budget, seeds, reserve, keys, profile, name, remove_saturated):
    output=ROOT/"protocols"/f"{name}.json"
    source=ROOT/screen
    rows=read(source)
    assert all(r["producer"]==producer for r in rows)
    arms={r["arm"] for r in rows}
    by={case:[r for r in rows if r["id"]==case] for case in sorted({r["id"] for r in rows})}
    excluded={}
    for case,block in by.items():
        if {r["status"] for r in block}=={"timeout"} and remove_saturated:
            excluded[case]="all screened arms timed out"
        if all(r["status"] in {"error","wrong","incomplete"} for r in block if r["arm"]=="0"):
            excluded[case]="failed default input contract"
    items=[x for x in manifest("train",producer,profile) if x["id"] not in excluded and "error" not in x]
    assert all(i["id"] in by for i in items)
    desc={"opt":"Static QF_LIA for synthetic discrete feasibility. Each case has an independent exact oracle.",
          "hw":"Live incremental Yosys SMTBMC. QF_AUFBV, model generation enabled, different families and both SAT and UNSAT answers."}
    config=dict(frozen_at=now(),name=name,producer=producer,
                protocol=dict(path=protocol,sha256=sha(ROOT/protocol)),
                source=dict(path=screen,sha256=sha(source)), profile=profile,
                description=desc[producer], cap=rows[0]["cap"],budget=budget,sol_batch=3,sol_timeout=65.,
                max_sol_calls=160,default_period=105,reserve=reserve,seeds=seeds,items=items,
                excluded=excluded,space=[dict(name=k,flags=MENU[producer][k]) for k in keys])
    if producer=="hw":
        config["feedback"]="scalar"
    if output.exists():
        old=read(output)
        assert {**old,"frozen_at":config["frozen_at"]}==config
    else:
        write(output,config)
    return output


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("producer")
    p.add_argument("screen")
    p.add_argument("protocol")
    p.add_argument("name")
    p.add_argument("--profile",default="base")
    p.add_argument("--budget",type=int,required=True)
    p.add_argument("--reserve",type=float,required=True)
    p.add_argument("--seeds",type=int,nargs="+",required=True)
    p.add_argument("--keys",nargs="+",required=True)
    p.add_argument("--remove-saturated",action="store_true")
    a=p.parse_args()
    target=make(a.producer,a.screen,a.protocol,a.budget,a.seeds,a.reserve,a.keys,a.profile,a.name,a.remove_saturated)
    print(target,sha(target),flush=True)
