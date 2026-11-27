"""Run smaller warm-method blocks after the resident-memory measurement."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import groupby
from pathlib import Path
import random
import shutil
import time

from screen import ROOT, base, CUTOFF, compile_driver, observe, run_batch, pair_summary, job, ARMS
from recover import host_sample, manifest

def prepare():
    source=ROOT/"build/src"
    dest=ROOT/"build/src-compact"
    dest.mkdir(exist_ok=True)
    for f in sorted(source.glob("*.kt")):
        p=dest/f.name
        if f.name=="WarmDriver.kt":
            s=f.read_text()
            old='            check(rssMb() <= 2850)'
            assert s.count(old)==1
            s=s.replace(old,'            System.gc()\n            check(rssMb() <= 3330)')
            if p.exists():
                assert p.read_text()==s
            else:
                p.write_text(s)
        elif p.exists():
            assert base.sha(f)==base.sha(p)
        else:
            shutil.copyfile(f,p)

def freeze():
    path=ROOT/"inputs/warm-compact.json"
    original=ROOT/"inputs/warm.json"
    if path.exists():
        return base.read(path)
    initial=base.read(original)
    rng=random.Random(230411)
    jobs=[]
    for i,old in enumerate(initial["jobs"]):
        tags=[]
        for tag, records in groupby(old["cells"],key=lambda r:r["tag"]):
            pieces=list(records)
            assert len(pieces)==len(ARMS)
            tags.append((tag,pieces))
        for n in range(0,len(tags),2):
            payload=[(tag,r["arm"]) for tag,recs in tags[n:n+2] for r in recs]
            jobs.append(job(f"{old['id']}-g{n//2+1}",old["project"],old["seed"],base.CPUS[(i+n//2)%4],
                        old["trace"],old["steps"],old["budget"],ARMS,payload))
    rng.shuffle(jobs)
    evidence=dict(at=base.now(),initial_sha=base.sha(original),
                  protocol=base.sha(ROOT.parent/"protocols/04b-warm-compact.md"),jobs=jobs)
    base.write(path,evidence)
    return evidence

def main():
    base.setup_limits()
    freeze1=base.read(ROOT/"inputs/split.json")["splits"]["train"]
    by_project={p["project"]:p for p in freeze1}
    design=freeze()
    assert design["protocol"]==base.sha(ROOT.parent/"protocols/04b-warm-compact.md")
    prepare()
    build=compile_driver("compact")
    raw=ROOT/"raw/warm-compact"
    evidence=ROOT/"evidence/warm-compact"
    raw.mkdir(exist_ok=True)
    evidence.mkdir(exist_ok=True)
    def on_core(core):
        observed={}
        for j in design["jobs"]:
            if j["core"]!=core or time.time()+155>=CUTOFF:
                continue
            location=raw/j["id"]
            if location.exists() and not (location/"status.json").is_file():
                location.rename(raw/f"{j['id']}-incomplete-{int(time.time())}")
            if not location.exists():
                base.ensure_disk()
                run_batch(location,j,by_project[j["project"]],build,cap=135,heap="1250m",rss_limit=3_450_000)
            result=observe(location,j)
            sealed=evidence/f"{j['id']}.json"
            if sealed.exists():
                assert base.read(sealed)==result
            else:
                base.write(sealed,result)
            observed[j["id"]]=result
            print("warm compact",j["id"],base.now(),result["complete"],result["state"]["watchdog"],flush=True)
        return observed
    with ThreadPoolExecutor(4) as pool:
        runs={k:v for lane in pool.map(on_core,base.CPUS) for k,v in lane.items()}
    used=[j for j in design["jobs"] if j["id"] in runs]
    good=[j for j in used if runs[j["id"]]["complete"] and runs[j["id"]]["verified"]]
    clocks=[runs[j["id"]]["state"] for j in used]
    theory=defaultdict(Counter)
    for j in good:
        if j["trace"]:
            for row in runs[j["id"]]["records"]:
                if row["arm"]=="A":
                    theory[j["project"]].update(row["stats"].get("theory_checks",{}))
    base.write(ROOT/"evidence/warm-compact-summary.json",dict(
        start=min((r["start_msk"] for r in clocks),default=base.now()),end=max((r["end_msk"] for r in clocks),default=base.now()),
        jvms=len(used),complete=len(good),failed=len(used)-len(good),
        watchdogs=Counter(r["watchdog"] or str(r["returncode"]) for r in clocks if r["watchdog"] or r["returncode"]!=0),
        projects=sorted({j["project"] for j in good}),host=host_sample(),theories=theory,
        table=pair_summary(good,runs),planned_table=pair_summary(used,runs)))

if __name__=="__main__":
    main()
