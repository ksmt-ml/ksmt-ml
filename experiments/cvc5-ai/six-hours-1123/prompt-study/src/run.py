"""An isolated train-only A/B of explicit effective option defaults."""
from datetime import datetime
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path[:0]=[str(TOP/"recovery/scripts"),str(TOP/"blind/src")]
import base
import request as blind

PROTOCOL=TOP/"protocols/22-option-delivery-prompt.md"
SOURCE=TOP/"array-tuning/raw/search"
CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()


def freeze():
    path=ROOT/"inputs/schedule.json"
    if not path.is_file():
        assert (TOP/"array-tuning/evidence/test-lock.json").is_file()
        rng=random.Random(23112201)
        first=["A","B"]
        rng.shuffle(first)
        jobs=[]
        for i in (1,2,3):
            p=SOURCE/f"r{i}-sol"
            call=p/"model/call-01/staged-train/training.json"
            if not call.is_file():
                continue
            data=base.read(call)
            assert not data["trials"] and data["baseline"]["n"]>0
            baseline=list((p/"baseline").glob("*/A/backend.json"))
            effective=[{k:base.read(f)["after_first_native_check"][k] for k in data["option_values"]} for f in baseline]
            assert len(baseline)==4 and all(d==effective[0] for d in effective)
            assert effective[0]=={"arrays-exp":"false","arrays-eager-index":"true","static-learning":"true"}
            dest=ROOT/"inputs"/f"train-r{i}.json"
            dest.write_bytes(call.read_bytes())
            order=first if i%2 else list(reversed(first))
            jobs.append(dict(id=f"r{i}",dataset=dest.name,sha=base.sha(dest),order=order,defaults=effective[0],
                             native_train_proofs={str(f.relative_to(TOP)):base.sha(f) for f in baseline}))
        base.write(path,dict(at=base.now(),jobs=jobs,protocol=base.sha(PROTOCOL)))
    plan=base.read(path)
    assert plan["protocol"]==base.sha(PROTOCOL)
    return plan


def main():
    base.setup_limits()
    plan=freeze()
    rows=[]
    consecutive_failures=0
    for j in plan["jobs"]:
        path=ROOT/"inputs"/j["dataset"]
        assert base.sha(path)==j["sha"]
        payload=base.read(path)
        setting=", ".join(f"{k}={v}" for k,v in sorted(j["defaults"].items()))
        condition=("The option_values map stores requested overrides, not current values. An omitted key keeps its factory value. "
            f"The train baseline effective settings after the first native cvc5 check are {setting}.\n")
        for arm in j["order"]:
            out=ROOT/"raw"/j["id"]/arm
            if not out.exists() and time.time()+51<CUTOFF and consecutive_failures<3:
                blind.invoke(out,payload,budget=45,instruction=condition if arm=="B" else "")
            if (out/"result.json").is_file():
                result=base.read(out/"result.json")
                assert base.sha(out/"staged-train/training.json")==j["sha"]
                options=result["answer"]["options"] if result["answer"] else []
                legal=bool(result["answer"] and options in payload["allowed"] and options)
                rows.append(dict(job=j["id"],arm=arm,legal=legal,included=legal and "arrays-exp" in options,**result))
                consecutive_failures=0 if legal else consecutive_failures+1
                print("blind prompt comparison",j["id"],arm,legal,base.now(),flush=True)
    if rows:
        base.write(ROOT/"evidence/summary.json",dict(start=min(r["status"]["start_msk"] for r in rows),
             end=max(r["status"]["end_msk"] for r in rows),source_packets=len(plan["jobs"]),planned=2*len(plan["jobs"]),
             calls=rows,by_arm={a:{"answers":sum(r["arm"]==a and r["legal"] for r in rows),
                                  "include_arrays_exp":sum(r["arm"]==a and r["included"] for r in rows)} for a in ("A","B")}))


if __name__=="__main__":
    main()
