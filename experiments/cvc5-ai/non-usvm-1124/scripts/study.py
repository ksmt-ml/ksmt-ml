"""Validation, irrevocable selection and holdout replay for one frozen plan."""
from concurrent.futures import ThreadPoolExecutor
import argparse
from pathlib import Path
import secrets
import time

from common import ROOT, CUTOFF, CPUS, now, read, sha, write
from eval import blocked, manifest, summarize
from search import flags, result

POLICY=ROOT/"protocols/05-lock-holdout.md"
COMMIT=ROOT/"protocols/holdout-generators.sha256"


def verify_commitment():
    for line in COMMIT.read_text().splitlines():
        digest,path=line.split()
        assert sha(ROOT/path)==digest,(path,digest)


def build(part,producer,release=None):
    if producer=="opt":
        from constraints import build as generate
        return generate(part,"base",release)
    if producer=="hw":
        if part=="validation":
            from hw_validation import build as generate
            return generate()
        from rtl import build as generate
        return generate(part,"base",CPUS[0],release)
    raise ValueError(producer)


def details(base, plan):
    head={}
    source=base/"search-summary.json"
    assert source.is_file()
    for info in read(source):
        if info["method"]=="default":
            continue
        seed,method=info["seed"],info["method"]
        head[f"{method}-{seed}"]=info["advancing"]
    assert set(head)=={f"{method}-{seed}" for method in ("smac","sol","random") for seed in plan["seeds"]}
    return head


def grouped(rows, ident):
    keep=[r for r in rows if r["arm"]==ident]
    return result(keep,keep)


def lock(source, contrast, reps):
    plan=read(source)
    base=ROOT/"raw"/plan["name"]
    dest=ROOT/"evidence"/f"{plan['name']}-lock.json"
    if dest.exists():
        saved=read(dest)
        assert saved["plan_sha256"]==sha(source) and saved["contrast"]==contrast
        return saved
    data=build("validation",plan["producer"])
    items=data["items"]
    head=details(base,plan)
    candidates={i for ids in head.values() for i in ids}
    arms={"0":[],"fixed":contrast}
    for i in sorted(candidates):
        arms[str(i)]=flags(plan["space"],i)
    lookup={}
    actual={}
    for name,options in arms.items():
        key=tuple(options)
        if key not in lookup:
            lookup[key]=name
            actual[name]=options
    rows=blocked(base/"validation",items,actual,float(plan["cap"]),reps,44911)
    stats={name:grouped(rows,lookup[tuple(options)]) for name,options in arms.items()}
    reference=stats["0"]
    selection={}
    for method,options in head.items():
        allowed=[i for i in options if stats[str(i)]["valid"]]
        i=min(allowed,key=lambda j:(stats[str(j)]["cost"],j)) if allowed else 0
        data=stats[str(i)]
        shared=sum(r["status"]=="solved" for r in rows if r["arm"]==lookup[tuple(arms[str(i)])])
        baseline_solved=reference["solved"]
        useful=((data["par2"]<.95*reference["par2"] and reference["par2"]-data["par2"]>=.02
                 and shared>=baseline_solved) or (shared>baseline_solved and data["par2"]<reference["par2"]))
        selection[method]=dict(id=i,flags=arms[str(i)],validation=data,deployment_pass=useful)
    evidence=dict(at=now(),producer=plan["producer"],plan_sha256=sha(source),policy_sha256=sha(POLICY),
                  generator_manifest_sha256=sha(COMMIT),search_sha256=sha(base/"search-summary.json"),
                  validation_sha256=sha(base/"validation/rows.json"),contrast=contrast,
                  validation_counts=summarize(rows),stat=stats,shortlists=head,selected=selection,
                  validation_sources_sha256=sha(ROOT/"scripts/hw_validation.py") if plan["producer"]=="hw" else None,
                  validation_items=len(items),validation_families=sorted(set(r["family"] for r in items)),reps=reps)
    write(dest,evidence)
    print(now(),"lock",dest.relative_to(ROOT),sha(dest),flush=True)
    return read(dest)


def reveal(source):
    verify_commitment()
    plan=read(source)
    producer=plan["producer"]
    locked=ROOT/"evidence"/f"{plan['name']}-lock.json"
    assert locked.is_file()
    frozen=read(locked)
    assert frozen["plan_sha256"]==sha(source) and frozen["policy_sha256"]==sha(POLICY)
    assert frozen["generator_manifest_sha256"]==sha(COMMIT)
    release=ROOT/"evidence"/f"{producer}-test-release.json"
    if release.exists():
        existing=read(release)
        assert existing["lock_sha256"]==sha(locked)
    else:
        write(release,dict(at=now(),lock=str(locked.relative_to(ROOT)),lock_sha256=sha(locked),
                           nonce=secrets.token_hex(32),mechanism="Linux getrandom at first post-lock release"))
    token=read(release)["nonce"]
    return build("test",producer,token)["items"]


def test(source,reps):
    plan=read(source)
    base=ROOT/"raw"/plan["name"]
    lock_file=ROOT/"evidence"/f"{plan['name']}-lock.json"
    locked=read(lock_file)
    complete=base/"test/score-map.json"
    if complete.is_file():
        checked=read(complete)
        assert checked["lock_sha256"]==sha(lock_file) and checked["reps"]==reps
        return checked
    items=reveal(source)
    cases=[case for case in items if "error" not in case]
    oracle=blocked(base/"test-oracle",cases,{"Z3":[]},40.,1,76663,"z3")
    mistaken={r["id"] for r in oracle if r["status"] in ("wrong","error","incomplete")}
    # cvc5 and Z3 are cross-checks; exact known answers remain available
    # when the latter reaches cap. Unsupported smoke transcripts are retained.
    cases=[r for r in cases if r["id"] not in mistaken]
    arms={"default":[],"fixed":locked["contrast"],**{k:r["flags"] for k,r in locked["selected"].items()}}
    lookup={}
    actual={}
    map_arms={}
    for name,options in arms.items():
        key=tuple(options)
        if key not in lookup:
            lookup[key]=name
            actual[name]=options
        map_arms[name]=lookup[key]
    path=base/"test"
    rows=blocked(path,cases,actual,float(plan["cap"]),reps,78429)
    intervals=oracle+rows
    write(path/"score-map.json",dict(start=min(r["start"] for r in intervals),
                                   stop=max(r["stop"] for r in intervals),recorded_at=now(),
                                   lock_sha256=sha(lock_file),configuration_aliases=map_arms,arms=arms,oracle_exclusions=sorted(mistaken),
                                   oracle=summarize(oracle),summary=summarize(rows),cap=plan["cap"],reps=reps,
                                   build_exclusions=[i["id"] for i in items if "error" in i]))
    print(now(),"test",summarize(rows),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("phase",choices=("validation","test"))
    p.add_argument("plan",type=Path)
    p.add_argument("--contrast",action="append",default=[])
    p.add_argument("--reps",default=2,type=int)
    a=p.parse_args()
    if a.phase=="validation":
        lock(a.plan.resolve(),a.contrast,a.reps)
    else:
        test(a.plan.resolve(),a.reps)
