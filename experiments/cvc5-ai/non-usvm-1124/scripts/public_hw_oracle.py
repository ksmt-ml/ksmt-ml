"""Retry independent public HW truth checks without touching cvc5 arms."""
from common import ROOT, now, read, sha, write
from eval import blocked, summarize

first=ROOT/"raw/public-hw/z3/rows.json"
prior=read(first)
todo={r["id"] for r in prior if r["status"]=="timeout"}
assert len(todo)==7,len(todo)
source=ROOT/"inputs/public/hw/manifest.json"
items=[r for r in read(source)["items"] if r["id"] in todo]
assert len(items)==len(todo)
out=ROOT/"raw/public-hw-oracle110"
rows=blocked(out,items,{"Z3":[]},110.,1,577602,"z3")
result=dict(at=now(),new=summarize(rows),initial=summarize(prior),registered=len(todo),
            match_total=sum(r["status"]=="solved" for r in prior+rows),
            errors=[r["id"] for r in rows if r["status"] in ("wrong","error","incomplete")],
            manifest_sha256=sha(source),prior_sha256=sha(first))
write(out/"summary.json",result)
print(result,flush=True)
