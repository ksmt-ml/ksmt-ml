"""Recover only SMAC's unstarted third-round candidate measurements."""
from datetime import datetime
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"array-tuning/src"))
import run as trial
from recover import manifest

base=trial.base
SCHEDULE=ROOT/"inputs/repair.json"
PROTOCOL=TOP/"protocols/23-smac-initialization.md"
ORIGINAL=TOP/"array-tuning/raw/search/r3-smac"
LOCK=TOP/"array-tuning/evidence/test-lock.json"
SMAC_INIT=TOP.parent/"campaign/.venv/lib/python3.12/site-packages/smac/utils/configspace.py"
SEED=2311203


def freeze_repair():
    assert not LOCK.exists(),"Define the technical recovery before the primary lock"
    first=base.read(ORIGINAL/"search.json")
    assert first["evaluations"]==0 and len(first["records"])==1 and first["records"][0]["mask"]==7
    files=[ORIGINAL/"search.json",ORIGINAL/"baseline/result.json",ORIGINAL/"budget.json",
           PROTOCOL,SMAC_INIT,TOP/"array-tuning/inputs/design.json"]
    frozen=dict(at=base.now(),source={str(f):base.sha(f) for f in files},seed=SEED,method="smac",original_forbidden=7)
    base.write(SCHEDULE,frozen)
    return frozen


def main():
    base.setup_limits()
    if "--freeze-only" in sys.argv:
        print(freeze_repair()["at"],flush=True)
        return
    proof=base.read(SCHEDULE)
    assert all(base.sha(Path(f))==s for f,s in proof["source"].items())
    assert datetime.fromisoformat(proof["at"])<=datetime.fromisoformat(base.read(LOCK)["at"])
    design=trial.freeze()
    _,engine,runtime,expected=trial.setup(design)
    contracts=base.read(TOP/"array-tuning/evidence/certified.json")
    allowed=[i for i in trial.ALLOWED if i==0 or contracts[f"m{i}"]["ok"]]
    original_baseline=base.read(ORIGINAL/"baseline/result.json")
    assert original_baseline["seed"]==SEED
    for row in original_baseline["rows"]:
        for a,v in row["arms"].items():
            assert manifest(ORIGINAL/"baseline"/row["job"]["id"]/a)==v["files"]
    old=base.read(ORIGINAL/"search.json")
    old_start=base.read(ORIGINAL/"budget.json")["start"]
    charged=(datetime.fromisoformat(old["end"])-datetime.fromisoformat(old_start)).total_seconds()
    baseline=trial.score(original_baseline,"B")
    where=ROOT/"raw"
    now=base.now()
    deadline=min(trial.CUTOFF,time.time()+trial.TRAIN-charged)
    def run_case(out,part,options,seed,end):
        return trial.run_set(out,part,options,seed,end,design,engine,runtime,expected)
    def evaluate(out,options,left):
        return trial.score(run_case(out,"train",{"A":{},"X":options},SEED,deadline),"X")
    progress=trial.optimizer.window(where/"search","smac",trial.VALUES,allowed,SEED,deadline-time.time(),evaluate,
         baseline["reference"],trial.PAIR_RESERVE,"Increase complete Java outcome sets while preserving work.")
    test_lock=ROOT/"evidence/test-lock.json"
    if not test_lock.is_file():
        until=min(trial.CUTOFF,time.time()+trial.VALIDATION)
        checks=[]
        for mask in progress["advanced"]:
            if until-time.time()<=trial.PAIR_RESERVE:
                break
            settings=trial.optimizer.options(mask,trial.VALUES)
            outcome=trial.score(run_case(where/"validation"/f"m{mask}","validation",{"A":{},"X":settings},SEED+110,until),"X")
            checks.append(dict(mask=mask,result=outcome))
        passed=sorted((r for r in checks if r["result"]["passes"]),key=lambda r:(r["result"]["cost"],r["mask"]))
        chosen=passed[0]["mask"] if passed else 0
        base.write(test_lock,dict(at=base.now(),chosen=chosen,options=trial.optimizer.options(chosen,trial.VALUES),checks=checks))
    chosen=base.read(test_lock)

    # Held-out metadata and outcomes are first opened after the recovery choice is sealed.
    source=base.read(TOP/"array-tuning/evidence/summary.json")
    label=next((a for a,v in source["options"].items() if v==chosen["options"]),None)
    reused=label is not None
    if reused:
        rows=[base.read(p) for p in sorted((TOP/"array-tuning/raw/test").glob("r*/result.json"))]
    else:
        label="X"
        options={"A":{},"E":trial.optimizer.options(1,trial.VALUES),"X":chosen["options"]}
        rows=[run_case(where/"test"/f"r{r+1}","test",options,231201+r,trial.CUTOFF) for r in range(4)]
    contrasts={b:[trial.score(x,label,b) for x in rows] for b in ("A","E") if b!=label}
    clocks=[x["state"] for d in rows for r in d["rows"] for x in r["arms"].values()] if not reused else []
    end=max([chosen["at"],*[s["end_msk"] for s in clocks]])
    base.write(ROOT/"evidence/summary.json",dict(start=progress["start"],end=end,measured_from=now,charged_seconds=charged,
                  search=progress,choice=chosen,original=old,heldout_label=label,test_reused=reused,test=contrasts,
                  protocol_sha=base.sha(PROTOCOL)))
    print("SMAC repaired",chosen["chosen"],reused,base.now(),flush=True)


if __name__=="__main__":
    main()
