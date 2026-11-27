"""Apply the preregistered TRAIN-only hardware gate."""
import argparse
import time

from analyze import screens
from common import ROOT, CPUS, CUTOFF, now, read, sha, write
from eval import MENU, blocked, manifest, summarize
from freeze_plan import make

p=argparse.ArgumentParser()
p.add_argument("--probe-only",action="store_true")
a=p.parse_args()
source=ROOT/"raw/screen-hw-seq-train-0-AA-internal-gauss-bool-prop-minisat-static-ite-repeat"
assert (source/"rows.json").is_file()
oracle_rows=blocked(ROOT/"raw/oracle-hw-seq-train",manifest("train","hw","seq"),{"Z3":[]},24.,1,273111,"z3",CPUS)
oracle_disagreements=[r["id"] for r in oracle_rows if r["status"] in ("wrong","error","incomplete")]
oracle_status=summarize(oracle_rows)
assert not oracle_disagreements,oracle_disagreements
panel=screens(source)
stock=panel["arms"]["0"]
baseline=stock["cluster_par2"]
noise=panel["median_aa_s"]
arms={}
for key,r in panel["arms"].items():
    if key=="0" or key=="AA":
        continue
    errors=sum(r["statuses"].get(k,0) for k in ("wrong","error","incomplete"))
    benefit=baseline-r["cluster_par2"]
    solved_diff=r["statuses"].get("solved",0)-stock["statuses"].get("solved",0)
    arms[key]=dict(solved_diff=solved_diff,delta=benefit,ratio=r["cluster_par2"]/baseline,
                   valid=not errors,gate=(not errors and ((benefit>.15*baseline and benefit>2*noise and solved_diff>=0)
                                                         or (solved_diff>=2 and benefit>0))))
qualified=[k for k,r in arms.items() if r["gate"]]
fixed="internal" if "internal" in qualified else min(qualified,key=lambda k:arms[k]["ratio"]) if qualified else "internal"
dest=ROOT/"evidence/hw-gate.json"
out=dict(at=now(),source_sha256=sha(source/"rows.json"),contrast=fixed,flags=MENU["hw"][fixed],
         passed=bool(qualified),measures=arms,stock=stock,noise=noise,qualified=qualified,oracle=oracle_status)
if dest.exists():
    existing=read(dest)
    assert {**existing,"at":out["at"]}==out
else:
    write(dest,out)
print(out,flush=True)
if not a.probe_only:
    keys=["internal","gauss","bool","prop","minisat","static","ite","repeat"]
    bad=[x for x in keys if not arms[x]["valid"]]
    available=max(0, int((CUTOFF-time.time()-1800)//1200))
    assert available>=1, "No 20-minute paired window with validation/holdout reserve"
    seeds=[2301,2302,2303][:min(3,available)] if qualified else [2301]
    make("hw",str((source/"rows.json").relative_to(ROOT)),"protocols/06-hw-seq.md",1200,seeds,21.,
         [x for x in keys if x not in bad],"seq","hw-search",True)
