"""Check every proposed option combination on tiny known SAT/UNSAT contracts."""
from pathlib import Path
import argparse

from common import ROOT, now, read, write
from eval import blocked, manifest
from search import flags

p=argparse.ArgumentParser()
p.add_argument("plan",type=Path)
p.add_argument("items",nargs="+")
p.add_argument("--cpus",type=int,nargs="+",default=[14,15])
p.add_argument("--contract-profile")
a=p.parse_args()
plan=read(a.plan)
source=manifest("train",plan["producer"],a.contract_profile) if a.contract_profile else plan["items"]
items=[r for r in source if r["id"] in set(a.items)]
assert len(items)==len(a.items)
arms={str(i):flags(plan["space"],i) for i in range(1<<len(plan["space"]))}
where=ROOT/"raw"/f"preflight-{plan['name']}"
rows=blocked(where,items,arms,4.,1,273170,cpus=tuple(a.cpus))
bad=[dict(id=r["id"],option=r["arm"],status=r["status"],location=r["location"]) for r in rows if r["status"]!="solved"]
write(where/"audit.json",dict(at=now(),cases=len(rows),rejected=bad,items=a.items))
print(now(),len(rows),len(bad),flush=True)
assert not bad,bad[:10]
