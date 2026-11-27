"""Audit hashes, timings, train-only proposal histories and blocked results."""
from collections import Counter, defaultdict
from datetime import datetime
import argparse
import json
from pathlib import Path

from common import ROOT, CPUS, now, read, sha, write

START=datetime.fromisoformat("2026-11-24T07:13:40+03:00")
STOP=datetime.fromisoformat("2026-11-24T11:08:00+03:00")


def check_date(s):
    x=datetime.fromisoformat(s)
    return START<=x<=STOP and x.utcoffset().total_seconds()==10800


def verify(deep=False):
    status=Counter()
    errors=[]
    discrepancies=[]
    ids={}
    labels={}
    primary_intervals=defaultdict(list)
    for p in (ROOT/"inputs").rglob("manifest.json"):
        manifest=read(p)
        for item in manifest.get("items",[]):
            if item.get("error"):
                status["input_build_error"]+=1
                continue
            path=ROOT/item["path"]
            ids.setdefault(item["id"],set()).add(item["sha256"])
            labels[item["id"],item["sha256"]]=item["expected"]
            if sha(path)!=item["sha256"]:
                errors.append((str(path),"input-hash"))
            if "source" in item and "source_hash" in item and sha(ROOT/item["source"])!=item["source_hash"]:
                errors.append((str(path),"producer-source-hash"))
            if "source_sha256" in item and sha(path.with_name("design.v"))!=item["source_sha256"]:
                errors.append((str(path),"source-hash"))
            if "oracle_sha256" in item and sha(path.with_name("oracle.json"))!=item["oracle_sha256"]:
                errors.append((str(path),"oracle-hash"))
            status["input"]+=1
    leaves=list((ROOT/"raw").rglob("result.json"))
    for r in leaves:
        row=read(r)
        if "id" not in row or "input_sha256" not in row:
            continue
        path=str(r.relative_to(ROOT))
        status["experiment_cells"]+=1
        status["cells_"+row["status"]]+=1
        if row["status"]=="wrong":
            discrepancies.append(dict(path=path,id=row["id"],engine=row["engine"],
                                      expected=row.get("expected"),observed=row.get("observed")))
        if row["input_sha256"] not in ids.get(row["id"],set()):
            errors.append((path,"input-hash-unregistered"))
        if row["producer"] in ("hw","opt") and row["expected"]!=labels.get((row["id"],row["input_sha256"])):
            errors.append((path,"expected-label-mismatch"))
        if row["producer"] in ("hw","opt"):
            primary_intervals[row["cpu"]].append((row["start"],row["stop"],path))
        if not check_date(row["start"]) or not check_date(row["stop"]) or row["stop"]<row["start"]:
            errors.append((path,"time-window"))
        if row["cpu"] not in CPUS:
            errors.append((path,"cpu"))
        process=read(r.with_name("process.json"))
        for key in ("cpu","cap","start","stop","seconds"):
            if process[key]!=row[key]:
                errors.append((path,"process-mismatch-"+key))
        expected_cost=row["seconds"] if row["status"]=="solved" else 2*row["cap"]
        if row["par2"]!=expected_cost:
            errors.append((path,"PAR2"))
        if deep:
            for name,digest in row["raw_sha256"].items():
                if sha(r.parent/name)!=digest:
                    errors.append((path,"raw-hash-"+name))
    for cpu,entries in primary_intervals.items():
        previous=None
        for start,stop,location in sorted(entries):
            if previous and start<previous[0]:
                delta=(datetime.fromisoformat(previous[0])-datetime.fromisoformat(start)).total_seconds()
                if delta>.002:
                    errors.append((location,"cpu-overlap",cpu,previous[1],delta))
            if previous is None or stop>previous[0]:
                previous=(stop,location)
    thread=set()
    for p in (ROOT/"raw").rglob("call.json"):
        r=read(p)
        if "packet_sha256" not in r:
            continue
        status["Sol_attempts"]+=1
        if r["status"]=="ok":
            status["Sol_success"]+=1
        if r["thread"]:
            if r["thread"] in thread:
                errors.append((str(p),"duplicate-Sol-thread"))
            thread.add(r["thread"])
        for key,source in (("packet_sha256","train/training.json"),("prompt_sha256","prompt.txt"),("events_sha256","client/stdout")):
            if sha(p.parent/source)!=r[key]:
                errors.append((str(p),"model-"+key))
        if not check_date(r["start"]) or not check_date(r["stop"]) or r["stop"]<r["start"]:
            errors.append((str(p),"model-time"))
        packet=read(p.parent/"train/training.json")
        if set(packet)!={"options","mode","cap","objective","history","batch"}:
            errors.append((str(p),"model-whitelist"))
        for item in packet["history"]:
            if set(item) not in ({"id","cost","solved","valid"},{"id","cost"}) or not isinstance(item["cost"],(int,float)):
                errors.append((str(p),"model-metrics"))
        if r["status"]=="ok" and (r["tool_events"] or not r["usage"] or r["model"]!="gpt-6.2-sol" or not r["thread"]):
            errors.append((str(p),"model-provenance"))
    for r in (ROOT/"evidence").glob("*-test-release.json"):
        state=read(r)
        lock=ROOT/state["lock"]
        if sha(lock)!=state["lock_sha256"] or not read(lock)["at"]<=state["at"]:
            errors.append((str(r),"holdout-order"))
        part=r.name.split("-")[0]
        m=ROOT/f"inputs/base/test/{part}/manifest.json"
        if m.is_file():
            materialized=read(m)
            if materialized["at"]<state["at"]:
                errors.append((str(m),"holdout-materialization"))
            release=datetime.fromisoformat(state["at"]).timestamp()
            for entry in materialized["items"]:
                if "path" in entry and (ROOT/entry["path"]).stat().st_mtime<release-.02:
                    errors.append((entry["path"],"pre-release-file"))
    for f in (ROOT/"protocols").glob("*-search.json"):
        p=read(f)
        raw=ROOT/"raw"/p["name"]
        if not (raw/"plan.json").exists():
            continue
        if read(raw/"plan.json")["sha256"]!=sha(f):
            errors.append((str(f),"changed-plan"))
        t=raw/"test/score-map.json"
        if t.is_file():
            test=read(t)
            lock_file=ROOT/"evidence"/f"{p['name']}-lock.json"
            locked=read(lock_file)
            actual=read(t.with_name("assignment.json"))["arms"]
            frozen={"default":[],"fixed":locked["contrast"],**{k:r["flags"] for k,r in locked["selected"].items()}}
            if test["lock_sha256"]!=sha(lock_file) or any(actual[test["configuration_aliases"][arm]]!=opts for arm,opts in frozen.items()):
                errors.append((str(t),"test-config-not-locked"))
        for d in sorted(raw.glob("s*")):
            if not d.is_dir():
                continue
            s=read(d/"schedule.json")
            if set(s["cpus"].values())!=set(CPUS) or s["end"]-s["start"]!=p["budget"]:
                errors.append((str(d),"search-budget-or-cpu"))
            for method,cpu in s["cpus"].items():
                f0=d/method/"origin.json"
                if f0.exists():
                    data=read(f0)
                    if data["cpu"]!=cpu or data["deadline"]!=s["end"] or data["planned_start"]!=s["start"]:
                        errors.append((str(f0),"search-origin"))
    output=dict(at=now(),counts=dict(status),distinct_Sol_threads=len(thread),errors=errors,raw_hashes=deep,
                primary_cpu_intervals={cpu:len(rows) for cpu,rows in primary_intervals.items()},
                discrepancies=discrepancies)
    write(ROOT/"evidence/artifact-audit.json",output)
    print(json.dumps({**output,"discrepancies":len(discrepancies)},ensure_ascii=False),flush=True)
    return not errors


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--deep",action="store_true")
    a=p.parse_args()
    if not verify(a.deep):
        raise SystemExit(1)
