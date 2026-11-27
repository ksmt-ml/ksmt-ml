"""Independent raw-log audit and paired cluster estimates.

This verifier does not import the acquisition, parsing or scoring code.
"""
import collections
import hashlib
import json
import math
import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT.parent/"live-client-141"
LIVE=ROOT/"raw/live"
ARM_NAMES=("default","smac","sol","z3")
FIELDS=("par2_ms","observed_wall_ms","paths","returned_paths","states","edges","coverage","steps","checks","check_ms",
        "failed","missing","unverified","method_errors","exploration_timeouts","step_errors",
        "solver_timeouts","solver_unknown","other_unknown")
EVENT_COUNTERS=("stops","terminal_exceptions","unknown_reasons","step_error_types")
WALL_KEYS=("wall_ms","batch_ms","index_ms","warmup_ms","teardown_ms")
TIMEOUT=re.compile(r"time.?out|time.?limit|^canceled$",re.I)
STEP=re.compile(r"\bStep failed\s*\n([^\n]+)")
ARM_CONFIG=re.compile(r"seat in this experiment")  # No model-based verification.
TZ=ZoneInfo("Europe/Moscow")

def stamp():
    return datetime.now(TZ).isoformat(timespec="milliseconds")

def read(f):
    return json.loads(f.read_text())

def json_lines(f):
    return [json.loads(line) for line in f.read_text(errors="replace").splitlines() if line.strip()]

def sha(f):
    with Path(f).open("rb") as buf:
        return hashlib.file_digest(buf,"sha256").hexdigest()

def put(f,data):
    temp=f.with_suffix(".tmp")
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
    temp.replace(f)

def date(x):
    return datetime.fromisoformat(x)

def rooted(url):
    return Path(unquote(urlparse(url).path))

def norm_drop(message):
    prefix=message.partition(": ")[0]
    if "fp.to_ieee_bv" in message:
        return prefix+" / fp.to_ieee_bv"
    if "couldn't be performed" in message or "is not allowed" in message:
        return prefix+" / StepScope"
    if "cannot be cast" in message:
        return prefix+" / cast"
    return prefix

def independent_cells(where,job,arm,options,runtime,release,baseline_options,digests):
    no_file=not (where/"status.json").exists()
    snap={} if no_file else read(where/"status.json")
    inv={} if no_file else read(where/"invocation.json")
    ok=(where/"backend.json").is_file()
    if not no_file:
        for f in sorted(where.iterdir()):
            if f.is_file():
                digests[str(f.relative_to(ROOT))]=sha(f)
        assert inv["config"]==options and inv["arm"]==arm and inv["core"]==job["core"]
        assert inv["seed"]==job["seed"] and inv["tags"]==job["tags"]
        assert (where/"methods.txt").read_text().splitlines()==job["tags"]
        assert inv["cmd"][:3]==["taskset","-c",str(job["core"])] and f"seed={job['seed']}" in inv["cmd"]
        assert inv["env"]["USVM_TABLE_ITE"]=="0" and inv["env"]["USVM_CLIENT_STATS"]=="1"
        assert inv["env"]["USVM_SOLVER"]==("z3" if arm=="z3" else "cvc5")
        assert inv["env"]["LIVE_SEARCH_OPTIONS"]==",".join(f"{k}={v}" for k,v in sorted(options.items()))
        assert date(snap["end_msk"])>=date(snap["start_msk"])
        delta=(date(snap["end_msk"])-date(snap["start_msk"])).total_seconds()*1000
        assert abs(delta-snap["wall_ms"])<80, (where,delta,snap)
        assert snap["cap_s"]==105 and 0<snap["wall_ms"]<114000
        cp=inv["cmd"][inv["cmd"].index("-cp")+1].split(":")
        assert str(OLD/"build/adapter") in cp and not any(Path(x).name.startswith("ksmt-cvc5") for x in cp)
        if (where/"usvm-options.txt").is_file():
            configured=(where/"usvm-options.txt").read_text()
            expected_parts=(f"randomSeed={job['seed']}","throwExceptionOnStepFailure=false","timeout=6s",
                            "solverTimeout=1.6s","typeOperationsTimeout=1s","stepLimit=4000",
                            "stepsFromLastCovered=3500","FORK_DEPTH","TRANSITIVE")
            assert all(token in configured for token in expected_parts),configured
    if ok:
        b=read(where/"backend.json")
        assert b["arm"]==inv["env"]["LIVE141_ARM"] and b["cpu"]==str(job["core"])
        assert b["processors"]==1 and b["java_runtime"].startswith("21.")
        mappings=(where/"backend-maps.txt").read_text().splitlines()
        assert {line.split(maxsplit=5)[-1] for line in mappings}==set(b["libraries"])
        assert all(sha(p)==h for p,h in b["libraries"].items())
        if arm=="z3":
            assert b["version"]=="4.13.0.0"
            assert {Path(p).name:h for p,h in b["libraries"].items()}==release["z3_native_sha256"]
            assert "ksmt-z3-core-0.5.26.jar" in str(rooted(b["adapter"]))
            assert "random_seed (unsigned int) random seed (default: 0)" in (where/"z3-help.txt").read_text()
        else:
            assert b["version"]=="1.4.1"
            assert b["libraries"]=={release["cvc5_jni_path"]:release["cvc5_jni_sha256"]}
            assert rooted(b["adapter"])==OLD/"build/adapter"
            assert sha(rooted(b["java_api"]))==release["cvc5_jar_sha256"]
            assert str(rooted(b["java_api"])) in runtime
            for phase in ("initial_options","after_first_native_check"):
                native=b[phase]
                for k,v in (baseline_options|options).items():
                    assert native[k]==v,(where,k,native[k],v)
                for k in ("incremental","produce-models","produce-unsat-cores"):
                    assert native[k]=="true",(where,k)
            assert b["after_first_native_check"]["tlimit-per"]=="1600"
            assert b["after_first_native_check"]["seed"]=="0"
    raw={}
    if (where/"results.jsonl").is_file():
        for line in (where/"results.jsonl").read_text(errors="replace").splitlines():
            try:
                r=json.loads(line)
            except json.JSONDecodeError:
                assert snap.get("watchdog") or snap.get("returncode")!=0
                continue
            assert r["tag"] in job["tags"] and r["tag"] not in raw
            assert r["arm"]==inv["env"]["LIVE141_ARM"] and r["wall_ms"]>=0
            raw[r["tag"]]=r
        assert list(raw)==job["tags"][:len(raw)]
    output=(where/"stdout.log").read_text(errors="replace") if (where/"stdout.log").exists() else ""
    pairs=re.split(r"(?m)^STOCK_METHOD_(START|END) ([^\n]+)\n",output)
    drops={}
    started=[]
    for i in range(1,len(pairs)-2,3):
        kind,tag,body=pairs[i:i+3]
        if kind=="START":
            started.append(tag)
            drops[tag]=collections.Counter(norm_drop(m.strip()) for m in STEP.findall(body))
    assert started==job["tags"][:len(started)],where
    marks={}
    if (where/"events.tsv").is_file():
        marks={label:float(ms) for label,_,ms,tag in (line.split("\t") for line in
               (where/"events.tsv").read_text().splitlines()) if not tag}
    result=[]
    for tag in job["tags"]:
        row=raw.get(tag)
        s=row["stats"] if row else {}
        cleanrow=bool(ok and row and row["status"]=="ok")
        info=s.get("unknown_reasons",{})
        timed=sum(n for text,n in info.items() if TIMEOUT.search(text))
        unknown=s.get("unknown",0)
        timeout=s.get("stop_reason")=="method_timeout"
        ds=drops.get(tag,{})
        dropped=sum(ds.values())
        bad=not cleanrow or timeout or bool(dropped) or bool(unknown)
        stop=s["stop_reason"] if cleanrow else ("method_error" if ok and row else "missing")
        if cleanrow:
            assert 0<=s["coverage_pct"]<=100 and 0<=s["returned_paths"]<=s["completed_paths"]<=s["terminated"]
            assert s["sat"]+s["unsat"]+s["unknown"]==s["checks"]
        result.append(dict(tag=tag,block=job["id"],project=job["project"],seed=job["seed"],
            par2_ms=16000 if bad else row["wall_ms"],
            observed_wall_ms=row["wall_ms"] if ok and row else 0,
            paths=s["completed_paths"] if cleanrow else 0,returned_paths=s["returned_paths"] if cleanrow else 0,
            edges=len(s["branch_edges"]) if cleanrow else 0,coverage=s["coverage_pct"] if cleanrow else 0,
            states=row["states"] if cleanrow else 0,steps=s["steps"] if cleanrow else 0,
            failed=int(bad),missing=int(row is None),unverified=int(row is not None and not ok),
            method_errors=int(ok and row is not None and row["status"]!="ok"),
            exploration_timeouts=int(cleanrow and timeout),step_errors=dropped,solver_unknown=unknown,
            solver_timeouts=timed,other_unknown=unknown-timed,
            checks=s.get("checks",0),check_ms=s.get("check_ms",0),stops={stop:1},present=cleanrow,
            unknown_reasons=info,step_error_types=ds,terminal_exceptions=s.get("exception_types",{})))
    def span(end,start):
        return marks[end]-marks[start] if end in marks and start in marks else None
    return dict(project=job["project"],id=job["id"],arm=arm,verified=ok,n=len(job["tags"]),cells=result,
                start=snap.get("start_msk"),end=snap.get("end_msk"),
                jvm_failed=int(not ok or not snap or snap["returncode"]!=0 or bool(snap["watchdog"])),
                rss_kib=snap.get("peak_rss_kib",0),wall_ms=snap.get("wall_ms"),teardown_ms=span("driver_end","teardown_start"),
                batch_ms=span("batch_end","batch_start"),index_ms=span("index_end","index_start"),
                warmup_ms=span("warmup_end","warmup_start"))

def totals(js):
    cells=[m for j in js for m in j["cells"]]
    v={k:sum(r[k] for r in cells) for k in FIELDS}
    for k in WALL_KEYS:
        vals=[j[k] for j in js if j[k] is not None]
        v[k]=sum(vals) if vals else None
        v[k+"_n"]=len(vals)
    for k in EVENT_COUNTERS:
        c=collections.Counter()
        for m in cells:
            c.update(m[k])
        v[k]=dict(c)
    return dict(v,n=len(cells),jvms=len(js),verified=sum(j["verified"] for j in js),
                jvm_failed=sum(j["jvm_failed"] for j in js))

def check_equal(x,y):
    assert x.keys()==y.keys(),(x.keys(),y.keys())
    for k in x:
        if isinstance(x[k],float):
            assert math.isclose(x[k],y[k],rel_tol=1e-10,abs_tol=1e-7),(k,x[k],y[k])
        else:
            assert x[k]==y[k], (k,x[k],y[k])

def assess(xs,ys):
    x,y=totals(xs),totals(ys)
    projects=set(j["project"] for j in xs)
    retained=all(100*x[k]>=98*y[k] for k in ("paths","edges","steps"))
    retained &= all(100*sum(r["paths"] for j in xs if j["project"]==p for r in j["cells"])
                    >=95*sum(r["paths"] for j in ys if j["project"]==p for r in j["cells"]) for p in projects)
    retained &= x["coverage"]-y["coverage"]>=-x["n"]
    reliable=all(x[k]<=y[k] for k in ("failed","step_errors","solver_unknown","solver_timeouts","jvm_failed"))
    full=not any(z["missing"] or z["unverified"] or z["verified"]!=z["jvms"] for z in (x,y))
    feasible=bool(retained and reliable and full)
    gain=x["par2_ms"]<=.98*y["par2_ms"]
    wall=x["wall_ms"] is not None and y["wall_ms"] is not None and x["wall_ms"]<=1.02*y["wall_ms"]
    return full,feasible,feasible and gain and wall

def percentile(v,q):
    if not v:
        return None
    pos=(len(v)-1)*q
    low=int(pos)
    return v[low]+(v[min(low+1,len(v)-1)]-v[low])*(pos-low)

def bootstrap(xs,ys):
    names=sorted({j["project"] for j in xs})
    byp={p:(totals([j for j in xs if j["project"]==p]),totals([j for j in ys if j["project"]==p])) for p in names}
    r=random.Random(22021141)
    draws=[r.choices(names,k=len(names)) for _ in range(10000)]
    def value(k,ps,pair=False):
        a,b=0,0
        n=0
        for p in ps:
            x,y=byp[p]
            if x[k] is None or y[k] is None:
                return None
            a+=x[k]
            b+=y[k]
            n+=x["n"]
        if pair:
            return (a-b)/n
        return a/b if b else None
    out={}
    for k in ("par2_ms","wall_ms","batch_ms","paths","returned_paths","edges","steps","coverage",
              "check_ms","checks"):
        is_difference=k=="coverage"
        sampler=sorted(b for b in (value(k,ps,is_difference) for ps in draws) if b is not None)
        out[k]=dict(value=value(k,names,is_difference),ci=[percentile(sampler,q) for q in (.025,.975)],
                    leave_one_out={p:value(k,[q for q in names if q!=p],is_difference) for p in names},
                    projects={p:value(k,[p],is_difference) for p in names},draws=len(sampler))
    return out

def main():
    start=stamp()
    manifest=read(ROOT/"inputs/manifest.json")
    schedule=read(ROOT/"inputs/schedule.json")
    sealed=read(ROOT/"inputs/seal.json")
    for n,p in (("manifest.json","inputs/manifest.json"),("schedule.json","inputs/schedule.json"),
                ("PREREG.md","PREREG.md"),("historical.json","evidence/historical.json")):
        assert sha(ROOT/p)==sealed[n]
    for p,h in manifest["earlier_files_rechecked"].items():
        assert sha(p)==h
    assert sum(len(v) for v in manifest["splits"].values())==len({p["project"] for v in manifest["splits"].values() for p in v})
    origin=read(OLD/"inputs/manifest.json")
    used={m["class"].split("$")[0] for p in origin["projects"] for m in p["methods"]}
    for p in manifest["splits"]["test"]:
        assert len(p["methods"])==8
        assert not {m["class"].split("$")[0] for m in p["methods"]}&used
    for projects in manifest["splits"].values():
        for p in projects:
            assert all(sha(a["path"])==a["sha256"] for a in [p["jar"],*p["dependencies"]])
    runtime=read(ROOT/"evidence/runtime.json")
    for p,h in runtime.items():
        assert sha(p)==h
    launch=read(LIVE/"launch.json")
    assert sha(ROOT/"inputs/seal.json")==launch["protocol"]
    assert sha(ROOT/"inputs/space.json")==launch["space_sha"]
    amendment_path=ROOT/"evidence/amendment-sol-stdin.json"
    supplement=read(amendment_path) if amendment_path.is_file() else None
    for p,h in launch["pkgs"].items():
        if supplement and p==supplement["file"]:
            assert sha(supplement["previous_copy"])==supplement["before"]==h
            assert sha(p)==supplement["after"]
            old=Path(supplement["previous_copy"]).read_text()
            current=Path(p).read_text()
            assert current[current.index("exec bwrap"):]==old[old.index("exec bwrap"):]
        else:
            assert sha(p)==h
    space=read(ROOT/"inputs/space.json")
    release=read(OLD/"runs/smoke/02/release.json")
    protocol=read(ROOT/"model/protocol/options.json")
    baseline=protocol["native_default_values"]
    toggles=list(protocol["non_default_values"])
    assert set(launch["allowed"])=={int(k) for k,v in space["combinations"].items() if k!="z3" and v["ok"]}
    digests={}
    blocks={}
    for f in sorted(LIVE.glob("**/assignment.json")):
        folder=f.parent
        a=read(f)
        section="test" if folder.name=="test" else "validation" if folder.name=="validation" else "train"
        untouched=lambda j:{k:v for k,v in j.items() if k!="order"}
        assert [untouched(j) for j in a["jobs"]]==[untouched(j) for j in schedule[section]]
        if section=="test":
            assert a["jobs"]==schedule[section]
        digests[str(f.relative_to(ROOT))]=sha(f)
        matrix={}
        for arm,options in a["arms"].items():
            matrix[arm]=[independent_cells(folder/j["id"]/arm,j,arm,options,runtime,release,baseline,digests)
                         for j in a["jobs"]]
        for ji,j in enumerate(a["jobs"]):
            prior=None
            assert set(j["order"])==matrix.keys()
            for arm in j["order"]:
                b=matrix[arm][ji]
                if not b["start"]:
                    continue
                assert prior is None or date(b["start"])>=date(prior)
                prior=b["end"]
        # Check all persisted online aggregates against this independent implementation.
        saved=read(folder/"summary.json") if (folder/"summary.json").exists() else {}
        for arm,js in matrix.items():
            if arm in saved:
                check_equal(totals(js),saved[arm])
        blocks[str(folder.relative_to(LIVE))]=matrix
    campaign={}
    for arm in ("smac","sol"):
        folder=LIVE/arm
        choice=read(folder/"choice.json")
        training=read(folder/"training.json") if (folder/"training.json").exists() else []
        baseline_train=blocks[f"{arm}/train-default"]["train"]
        for t in training:
            i=t["mask"]
            measured=blocks[f"{arm}/train-{i:02d}"]["train"]
            res=assess(measured,baseline_train)
            assert res==tuple(t["result"][k] for k in ("complete","feasible","admissible"))
            assert i in launch["allowed"] and read(folder/f"train-{i:02d}"/"assignment.json")["arms"]["train"]==space["combinations"][str(i)]["options"]
        val=blocks.get(f"{arm}/validation")
        vresults=choice["validation"]
        for i,t in enumerate(vresults):
            res=assess(val[f"candidate{i+1}"],val["default"])
            assert res==tuple(t["result"][k] for k in ("complete","feasible","admissible"))
        winner=min((t for t in vresults if t["result"]["admissible"]),key=lambda t:(t["result"]["par2_ratio"],t["mask"]),default=None)
        assert choice["mask"]==(winner["mask"] if winner else 0)
        assert choice["options"]==space["combinations"][str(choice["mask"])]["options"]
        assert date(choice["end_msk"])>=date(choice["start_msk"])
        assert choice["wall_s"]<=choice["max_wall_s"]+2
        rec={"choice":choice,"training_baseline":totals(baseline_train),
             "validation_baseline":totals(val["default"]) if val else None}
        if arm=="sol":
            chronology=[]
            starts={t["mask"]:t["start"] for t in training}
            previous_end=None
            for call in sorted((folder/"model").glob("call-*")):
                snap=read(call/"status.json")
                inv=read(call/"invocation.json")
                assert sha(call/"observations.json")==inv["snapshot_sha"]
                assert snap["cap_s"]<=80 and (previous_end is None or date(snap["start_msk"])>=date(previous_end))
                previous_end=snap["end_msk"]
                data=read(call/"observations.json")
                effective=call/"effective-prompt.txt"
                inline=effective.is_file()
                if supplement and call.name>=supplement["effective_from_call"]:
                    assert inline and date(snap["start_msk"])>=date(supplement["at_msk"])
                    payload=effective.read_text()
                    assert payload.startswith((call/"prompt.txt").read_text())
                    assert payload.endswith((call/"observations.json").read_text())
                elif supplement:
                    assert not inline and call.name==supplement["first_call"]
                for t in data["trials"]:
                    mask=sum(1<<toggles.index(k) for k in t["options"])
                    assert mask in starts and date(next(t["end"] for t in training if t["mask"]==mask))<=date(snap["start_msk"])
                    live=t["train"]
                    recorded=next(t["result"]["totals"] for t in training if t["mask"]==mask)
                    for k in live:
                        assert round(recorded[k],3)==live[k]
                events=[]
                for line in (call/"stdout.log").read_text(errors="replace").splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
                complete=any(x.get("type")=="turn.completed" for x in events)
                assert not any(t for t in ("validation","holdout","historical") if t in (call/"observations.json").read_text())
                proposal=read(call/"proposal.json")
                for f in (call/"proposal.json",call/"observations.json",call/"status.json",call/"stdout.log",effective):
                    if f.is_file():
                        digests[str(f.relative_to(ROOT))]=sha(f)
                chronology.append(dict(call=call.name,start=snap["start_msk"],end=snap["end_msk"],
                    train_trials_seen=len(data["trials"]) if inline else 0,inline_train=inline,completed=complete,mask=proposal["mask"],status=snap["returncode"],
                    watchdog=snap["watchdog"]))
            rec["inference"]=chronology
        else:
            log=read(folder/"ask-tell.json") if (folder/"ask-tell.json").exists() else []
            rec["asks"]=len(log)
            assert len(log)>=len(training)
            runtimes=list((folder/"smac-output").rglob("runhistory.json"))
            assert len(runtimes)==1
            rh=read(runtimes[0])
            assert rh["stats"]["finished"]==len(rh["data"])==len(log)
            for t,actual in zip(log,rh["data"],strict=True):
                option_mask=sum(int(rh["configs"][str(actual["config_id"])][k])<<i for i,k in enumerate(toggles))
                assert t["mask"]==option_mask and t["seed"]==actual["seed"]
                assert math.isclose(t["cost"],actual["cost"],rel_tol=1e-12)
            rec["smac_runhistory"]=str(runtimes[0].relative_to(ROOT))
            digests[rec["smac_runhistory"]]=sha(runtimes[0])
        campaign[arm]=rec
    lock=read(LIVE/"test-lock.json")
    assert lock["schedule_sha"]==sha(ROOT/"inputs/schedule.json")
    for a,h in lock["choices"].items():
        assert sha(LIVE/a/"choice.json")==h
        assert lock["configs"][a]==campaign[a]["choice"]["options"]
    test=blocks["test"]
    assert read(LIVE/"test/assignment.json")["arms"]==lock["configs"]
    assert all(date(j["start"])>=date(lock["locked"]) for js in test.values() for j in js if j["start"])
    contrast={a:bootstrap(test[a],test["default"]) for a in ("smac","sol","z3")}
    measured={a:totals(js) for a,js in test.items()}
    for a,s in measured.items():
        assert len(test[a])==16 and s["n"]==128
    tests=read(LIVE/"test-contrasts.json")
    gates={}
    for a in ("smac","sol","z3"):
        full,feasible,admissible=assess(test[a],test["default"])
        assert (full,feasible,admissible)==tuple(tests[a][k] for k in ("complete","feasible","admissible"))
        gates[a]=dict(complete=full,feasible=feasible,admissible=admissible,violations=tests[a]["violations"])
    all_jvms=[j for package in blocks.values() for js in package.values() for j in js]
    per_project={}
    for project in sorted({j["project"] for j in test["default"]}):
        per_project[project]={a:totals([j for j in js if j["project"]==project]) for a,js in test.items()}
    write_path=ROOT/"evidence/verification.json"
    put(write_path,dict(start_msk=start,end_msk=stamp(),verified=True,jvms=len(all_jvms),
        verified_jvms=sum(j["verified"] for j in all_jvms),max_rss_kib=max(j["rss_kib"] for j in all_jvms),
        test=measured,test_by_project=per_project,contrasts=contrast,gates=gates,arms=campaign,
        digests=digests,sol_stdin_amendment=supplement,pipeline_start=launch["start"],lock=lock["locked"],test_end=max(j["end"] for js in test.values() for j in js if j["end"])))
    test_rows=[dict(arm=a,**m) for a,js in test.items() for j in js for m in j["cells"]]
    put(ROOT/"evidence/test-by-method.json",test_rows)
    print(write_path,len(all_jvms),"JVM")

if __name__=="__main__":
    main()
