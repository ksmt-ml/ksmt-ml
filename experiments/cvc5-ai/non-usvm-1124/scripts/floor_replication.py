"""Repeat all locked QF_LIA settings with an exact default alias."""
from collections import defaultdict
from statistics import median

from common import ROOT, now, read, sha, write
from eval import blocked, manifest, summarize

MAIN=ROOT/"raw/opt-search/test"
LOCK=ROOT/"evidence/opt-search-lock.json"
PLAN=ROOT/"protocols/08-floor-replication.md"

score=read(MAIN/"score-map.json")
first=read(MAIN/"rows.json")
assert first and all(r["status"]=="solved" for r in first), "replication is conditioned on the fully solved floor"
actual=read(MAIN/"assignment.json")["arms"]
assert actual["default"]==[]
items=[r for r in manifest("test","opt","base") if r["id"] not in score["oracle_exclusions"] and "error" not in r]
where=ROOT/"raw/floor-replication"
rows=blocked(where,items,{**actual,"AA":[]},7.,8,184204)
pairs=defaultdict(dict)
for r in rows:
    if r["arm"] in {"AA","default"} and r["status"]=="solved":
        pairs[r["id"],r["rep"]][r["arm"]]=r["seconds"]
abs_gap=[abs(r["AA"]-r["default"]) for r in pairs.values() if len(r)==2]
results=dict(start=min(r["start"] for r in rows),stop=max(r["stop"] for r in rows),at=now(),
             lock_sha256=sha(LOCK),protocol_sha256=sha(PLAN),reps=8,known_pairs=len(abs_gap),
             median_alias_gap_s=median(abs_gap) if abs_gap else None,summary=summarize(rows))
write(where/"summary.json",results)
print(results,flush=True)
