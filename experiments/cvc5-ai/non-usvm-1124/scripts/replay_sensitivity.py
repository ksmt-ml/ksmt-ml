"""Compare locked cvc5 settings on completed exact Yosys stdin transcripts."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import re

from common import ROOT, CVC, CPUS, environment, now, ordered, read, run, sha, write
from eval import ANSWER, ERROR
from hw_audit import commands

audit=ROOT/"raw/hw-wire-base-test-stock"
source=audit/"rows.json"
info=read(source)
lock_path=ROOT/"evidence/hw-search-lock.json"
lock=read(lock_path)
output=ROOT/"raw/live-replay-test"
excluded=[]
accepted={}
for r in info:
    if r.get("wire_dump_same_commands") is not True or r.get("same_answers") is not True:
        excluded.append(r["id"])
        continue
    path=audit/r["id"]/"capture/wire.smt2"
    assert sha(path)==r["wire_in_sha256"]
    assert sum(1 for c in commands(path,False) if c[:2]==["(","check-sat"])==len(r["cvc5"])
    accepted[r["id"]]=(path,r)

plans=[]
flags={"0":[],"fixed":lock["contrast"]}
for rep in range(2):
    for idx,case in enumerate(ordered(accepted,88241,rep)):
        plans.append(dict(rep=rep,case=case,cpu=CPUS[(idx+rep)%len(CPUS)],arms=ordered(flags,88241,case,rep)))
assignment=output/"assignment.json"
schedule=dict(plans=plans,flags=flags,cap=8.,wire_audit=sha(source),lock_sha256=sha(lock_path))
if assignment.exists():
    assert read(assignment)==schedule
else:
    write(assignment,schedule)

def core(cpu):
    results=[]
    for job in (j for j in plans if j["cpu"]==cpu):
        path,expect=accepted[job["case"]]
        for arm in job["arms"]:
            opts=flags[arm]
            where=output/f"r{job['rep']}"/job["case"]/arm
            r=run(where,[CVC,"--incremental","--lang=smt2",*opts,path],cpu,8.,environment())
            stdout=(where/"stdout").read_text()
            stderr=(where/"stderr").read_text()
            observed=ANSWER.findall(stdout)
            wrong=any(x in {"sat","unsat"} and x!=y for x,y in zip(observed,expect["cvc5"]))
            solved=(observed==expect["cvc5"] and not r["timeout"] and r["ret"]==0 and not ERROR.search(stdout+"\n"+stderr))
            results.append(dict(arm=arm,case=job["case"],family=expect["family"],rep=job["rep"],wire_sha256=sha(path),
                                cpu=cpu,start=r["start"],stop=r["stop"],expected=expect["cvc5"],answers=observed,
                                seconds=r["seconds"],par2=r["seconds"] if solved else 16.,solved=solved,wrong=wrong))
    return results

with ThreadPoolExecutor(max_workers=4) as pool:
    rows=[r for sub in pool.map(core,CPUS) for r in sub]
write(output/"rows.json",rows)

from statistics import mean
summary={}
for arm in flags:
    rs=[r for r in rows if r["arm"]==arm]
    summary[arm]=dict(n=len(rs),solved=sum(r["solved"] for r in rs),wrong=sum(r["wrong"] for r in rs),error=0,
                      par2=mean(r["par2"] for r in rs) if rs else None)
write(output/"summary.json",dict(at=now(),summary=summary,excluded=excluded,cases=len(accepted),
                                 evidence=sha(assignment),scope="separate CLI replay"))
print(now(),summary,len(excluded),flush=True)
