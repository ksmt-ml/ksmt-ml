"""A secondary equal-cap contrast on the previously selected original public IDL files."""
from common import ROOT,now,read,sha,write
from eval import blocked,summarize

source=ROOT/"raw/public-opt/summary.json"
panel=read(source)
lock=ROOT/"evidence/opt-search-lock.json"
assert panel["n"]==16 and panel["oracle"]["Z3"]["solved"]==16
assert panel["lock_sha256"]==sha(lock)
baseline=panel["summary"]["default"]
assert baseline["solved"]<baseline["n"]
items=[r for r in read(ROOT/"inputs/public/opt/manifest.json")["items"] if r["id"] not in panel["excluded"]]
out=ROOT/"raw/public-idl-cap30"
rows=blocked(out,items,{"default":[],"fixed":read(lock)["contrast"]},30.,1,662204)
result=dict(at=now(),start=min(r["start"] for r in rows),stop=max(r["stop"] for r in rows),
            summary=summarize(rows),n=len(items),lock_sha256=sha(lock),seed=662204,excluded=panel["excluded"])
write(out/"summary.json",result)
print(result,flush=True)
