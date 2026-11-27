"""Audit the supplementary matrix and associate ordinary USVM step exceptions."""
import csv
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime

from common import ARMS, ROOT, now, read, sha, write
import verify

RAW=ROOT/"runs/stock"
ORIGINAL=ROOT/"runs/primary"

def line_events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]

def parse_step_logs(path, tag_order):
    errors=defaultdict(list)
    current=None
    seen=[]
    pending=False
    messages={}
    text=path.read_text(errors="replace")
    for line in text.splitlines():
        if line.startswith("STOCK_METHOD_START "):
            assert current is None
            current=line.removeprefix("STOCK_METHOD_START ")
            seen.append(current)
        elif line.startswith("STOCK_METHOD_END "):
            assert line.removeprefix("STOCK_METHOD_END ") == current
            current=None
            pending=False
        elif pending:
            info=line.strip()
            kind,_,detail=info.partition(": ")
            if "fp.to_ieee_bv" in detail:
                detail="fp.to_ieee_bv"
            elif "cannot be cast to class org.usvm.UConcreteHeapRef" in detail:
                detail="cast to UConcreteHeapRef"
            elif len(detail)>120:
                detail=detail[:120]
            kind=f"{kind}: {detail}" if detail else kind
            messages.setdefault(kind,info[:240])
            errors[current].append(kind)
            pending=False
        elif current and re.search(r"\bStep failed\s*$",line):
            pending=True
    assert seen==tag_order[:len(seen)]
    return dict(errors), messages, seen

def main():
    if not read(RAW/"decision.json")["run"]:
        return
    lock=read(RAW/"lock.json")
    feats=read(RAW/"metrics.json")
    records=line_events(RAW/"supervisor.jsonl")
    fitted=read(RAW/"derived-schedule.json")
    original=read(ROOT/"inputs/schedule.json")
    assert fitted["primary_schedule_sha256"]==sha(ROOT/"inputs/schedule.json")
    assert sha(RAW/"derived-schedule.json")==lock["derived_schedule"]
    assert sha(ORIGINAL/"metrics.json")==lock["main_input"]
    assert records[0]["event"]=="campaign_start" and records[-1]["event"]=="campaign_end"
    assert datetime.fromisoformat(records[0]["at"]) >= datetime.fromisoformat(read(ORIGINAL/"metrics.json")["end"])
    assert len(fitted["jobs"])==30
    for p,h in lock["stock_overlay"].items():
        assert sha(ROOT/p)==h
    check_source=next(ROOT/p for p in lock["stock_overlay"] if p.endswith(".kt"))
    original_source=ROOT/"checkout/usvm/usvm-jvm/src/test/kotlin/org/usvm/dataset/Live141Driver.kt"
    stripped="\n".join(x for x in check_source.read_text().splitlines() if 'println("STOCK_METHOD_' not in x)
    assert stripped+"\n" == original_source.read_text().replace("throwExceptionOnStepFailure = true","throwExceptionOnStepFailure = false")
    for project in feats["projects"]:
        subset=[j for j in fitted["jobs"] if j["project"]==project]
        opening=next(j for j in original["jobs"] if j["project"]==project)
        assert opening in subset and len(subset)==3
        assert all({j["arms"][pos] for j in subset}==set(ARMS) for pos in range(3))
    jvm={(e["block"],e["arm"]):e for e in records if e["event"]=="case_end"}
    affected={a:Counter() for a in ARMS}
    observations=Counter()
    notes={}
    raw_digests={}
    scores={a:defaultdict(list) for a in ARMS}
    method_store=defaultdict(Counter)
    for job in fitted["jobs"]:
        expect=next(j for j in original["jobs"] if j["id"]==job["id"])
        assert expect==job
        prior_end=None
        options={}
        for arm in job["arms"]:
            p=RAW/job["id"]/arm
            st=read(p/"status.json") if (p/"status.json").exists() else None
            ok=bool(jvm.get((job["id"],arm),{}).get("identity_verified"))
            errs={}
            if st:
                assert st["cap_s"]==150 and st["core"]==job["core"]
                assert prior_end is None or datetime.fromisoformat(prior_end)<=datetime.fromisoformat(st["start_msk"])
                prior_end=st["end_msk"]
                if ok:
                    options[arm]=verify.backend(p,arm,job["core"],lock["release"])
                    opts=(p/"usvm-options.txt").read_text()
                    normal=(ORIGINAL/job["id"]/arm/"usvm-options.txt").read_text()
                    assert opts == normal.replace("throwExceptionOnStepFailure=true","throwExceptionOnStepFailure=false")
                errs, messages, seen=parse_step_logs(p/"stdout.log",job["tags"])
                if st["returncode"]==0:
                    assert seen==job["tags"]
                notes.update(messages)
            rows={r["tag"]:r for r in verify.lines(p/"results.jsonl")}
            strict_rows={r["tag"]:r for r in verify.lines(ORIGINAL/job["id"]/arm/"results.jsonl")}
            for tag in job["tags"]:
                r=rows.get(tag)
                strict_error = tag in strict_rows and strict_rows[tag]["status"] != "ok"
                observations[arm,"primary_same_cells_error"] += strict_error
                observations[arm,"stock_returned_after_strict_error"] += bool(strict_error and ok and r and r["status"]=="ok")
                if r:
                    verify.check_stats(r)
                errors=errs.get(tag,[])
                affected[arm].update(errors)
                observations[arm,"started_with_step_failure"]+=bool(errors)
                method_store[arm,tag]["appearances"]+=1
                method_store[arm,tag]["runs_with_step_failure"]+=bool(errors)
                usable=ok and r and r["status"]=="ok" and r["stats"]["stop_reason"]!="method_timeout"
                observations[arm,"budget_complete_with_step_failure"]+=bool(usable and errors)
                scores[arm][job["project"]].append(r["wall_ms"] if usable and not errors else 16000)
            for f in p.glob("*"):
                if f.is_file(): raw_digests[str(f.relative_to(ROOT))]=sha(f)
        if all(a in options for a in ("cvc5-default","cvc5-none")):
            verify.verify_option_delta(options["cvc5-default"],options["cvc5-none"])
    all_group=feats["projects"]
    rng=random.Random(504141)
    draws=[rng.choices(all_group,k=len(all_group)) for _ in range(10000)]
    risks={}
    for a,b in (("cvc5-none","cvc5-default"),("cvc5-default","z3"),("cvc5-none","z3")):
        totals={side:{g:sum(values) for g,values in scores[side].items()} for side in (a,b)}
        get_ratio=lambda g: sum(totals[a][k] for k in g)/sum(totals[b][k] for k in g)
        sorted_draws=sorted(get_ratio(draw) for draw in draws)
        risks[f"{a}/{b}"]={"value":get_ratio(all_group),"ci":[verify.quantile(sorted_draws,x) for x in (.025,.975)]}
    rows=[dict(arm=a,tag=tag,**dict(c)) for (a,tag),c in method_store.items() if c["runs_with_step_failure"]]
    c=dict(ok=True,verified_at_msk=now(),recorded_jvms=len(jvm),planned_jvms=90,
           method_step_failures={a:dict(affected[a]) for a in ARMS},first_examples=notes,
           observations={a:{k:n for (arm,k),n in observations.items() if arm==a} for a in ARMS},
           affected_methods=rows,par2_with_step_failure=risks,metrics_sha256=sha(RAW/"metrics.json"),
           raw_file_sha256=raw_digests)
    write(RAW/"VERIFY.json",c)
    print("Verified supplementary", len(jvm), "JVMs and",len(raw_digests),"raw files")
    print("Step failures:",{a:sum(affected[a].values()) for a in ARMS})

if __name__=="__main__":
    main()
