"""Re-evaluate a locked hardware fixed contrast on a longer live BMC horizon."""
from common import ROOT, now, read, sha, write
from eval import blocked, manifest, summarize
from analyze import HW_SEQ

primary=ROOT/"raw/hw-search/test/score-map.json"
lock_path=ROOT/"evidence/hw-search-lock.json"
plan=read(primary)
frozen=read(lock_path)
assert plan["lock_sha256"]==sha(lock_path)
out=ROOT/"raw/horizon-transfer"
items=[dict(r,depth=r["depth"]+4) for r in manifest("test","hw","base")
       if "error" not in r and r["id"] not in plan["oracle_exclusions"] and r["family"] in HW_SEQ]
raw_oracle=blocked(out/"oracle",items,{"Z3":[]},25.,1,43910,"z3")
disagreed={r["id"] for r in raw_oracle if r["status"] in ("wrong","error","incomplete")}
usable=[r for r in items if r["id"] not in disagreed]
rows=blocked(out/"cvc5",usable,{"0":[],"AA":[],"fixed":frozen["contrast"]},12.,2,43911)
interval=raw_oracle+rows
result=dict(at=now(),start=min(r["start"] for r in interval),stop=max(r["stop"] for r in interval),
            summary=summarize(rows),excluded=sorted(disagreed),n=len(usable),oracle=summarize(raw_oracle),
            depth_offset=4,lock_sha256=sha(lock_path),inheritance="same source systems, new live query sequence")
write(out/"summary.json",result)
print(result,flush=True)
