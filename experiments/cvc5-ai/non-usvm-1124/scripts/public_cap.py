"""Longer-cap fixed-vs-stock transfer on the unchanged public hardware files."""
from common import ROOT, now, read, sha, write
from eval import blocked, summarize

source=ROOT/"raw/public-hw/summary.json"
panel=read(source)
assert panel["n"]==8 and all(r["solved"]==0 for r in panel["summary"].values())
locked=ROOT/"evidence/hw-search-lock.json"
assert panel["lock_sha256"]==sha(locked)
manifest=read(ROOT/"inputs/public/hw/manifest.json")
items=[r for r in manifest["items"] if r["id"] not in panel["excluded"]]
out=ROOT/"raw/public-hw-cap45"
rows=blocked(out,items,{"default":[],"fixed":read(locked)["contrast"]},45.,1,662103)
result=dict(at=now(),start=min(r["start"] for r in rows),stop=max(r["stop"] for r in rows),
            summary=summarize(rows),n=len(items),lock_sha256=sha(locked),seed=662103,excluded=panel["excluded"])
write(out/"summary.json",result)
print(result,flush=True)
