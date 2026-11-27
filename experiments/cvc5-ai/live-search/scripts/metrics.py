import re
import statistics
from collections import Counter
from base import *
from historical import step_logs

TIMEOUT = re.compile(r"time.?out|time.?limit|^canceled$", re.I)
ADDITIVE = ("par2_ms","paths","returned_paths","edges","coverage","steps","checks","check_ms",
            "solver_timeouts","solver_unknown","other_unknown","step_errors","failed","missing",
            "exploration_timeouts","method_errors","unverified","states","observed_wall_ms")

def rows_from(directory):
    path=directory/"results.jsonl"
    found={}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row=json.loads(line)
            except json.JSONDecodeError:
                continue
            if row["tag"] in found:
                raise ValueError(f"duplicate method {row['tag']} in {path}")
            found[row["tag"]]=row
    return found

def observe_block(path,job):
    expected=read(EXPECTED)
    status_path=path/"status.json"
    if status_path.exists():
        st=read(status_path)
        inv=read(path/"invocation.json")
        rows=rows_from(path)
        errors=step_logs((path/"stdout.log").read_text(errors="replace"))
        verified=False
        if (path/"backend.json").exists():
            validate_backend(path,read(path/"backend.json"),inv,expected)
            verified=True
    else:
        st,rows,errors,verified={}, {}, {}, False
    methods=[]
    for tag in job["tags"]:
        r=rows.get(tag)
        s=r["stats"] if r else {}
        recorded=bool(r and verified)
        visible=recorded and r["status"]=="ok"
        drops=sum(errors.get(tag,{}).values())
        unknown=s.get("unknown",0)
        timeouts=sum(v for k,v in s.get("unknown_reasons",{}).items() if TIMEOUT.search(k))
        exploration=s.get("stop_reason")=="method_timeout"
        failed=not visible or bool(drops or unknown or exploration)
        methods.append(dict(tag=tag, par2_ms=PENALTY if failed else r["wall_ms"],
            observed_wall_ms=r["wall_ms"] if recorded else 0, paths=s.get("completed_paths",0) if visible else 0,
            returned_paths=s.get("returned_paths",0) if visible else 0,
            states=r["states"] if visible else 0,
            edges=len(s.get("branch_edges",[])) if visible else 0,
            coverage=s.get("coverage_pct",0) if visible else 0,
            steps=s.get("steps",0) if visible else 0,checks=s.get("checks",0),check_ms=s.get("check_ms",0),
            step_errors=drops, step_error_types=dict(errors.get(tag,{})), failed=int(failed),
            solver_timeouts=timeouts, solver_unknown=unknown,other_unknown=unknown-timeouts,
            unknown_reasons=s.get("unknown_reasons",{}),
            missing=int(not r), unverified=int(bool(r) and not verified),
            exploration_timeouts=int(visible and exploration),
            method_errors=int(recorded and r["status"]!="ok"),
            terminal_exceptions=s.get("exception_types",{}),
            stop=s.get("stop_reason") if visible else ("method_error" if recorded else "missing"),
            message=r["message"] if recorded else ""))
    crashes=int(not st or st["returncode"]!=0 or bool(st["watchdog"]) or not verified)
    phase={}
    event_file=path/"events.tsv"
    if event_file.exists():
        for line in event_file.read_text().splitlines():
            label,_,ms,_=line.split("\t")
            if label not in ("method_start","method_end"):
                phase[label]=float(ms)
    diffs={}
    for name,a,b in (("batch_ms","batch_start","batch_end"),("index_ms","index_start","index_end"),
                     ("warmup_ms","warmup_start","warmup_end"),("teardown_ms","teardown_start","driver_end")):
        diffs[name]=phase[b]-phase[a] if a in phase and b in phase else None
    return dict(project=job["project"],block=job["id"],wall_ms=st.get("wall_ms"),jvm_failed=crashes,
                verified=verified,methods=methods,**diffs)

def aggregate(blocks):
    methods=[r for b in blocks for r in b["methods"]]
    sums={k:sum(m[k] for m in methods) for k in ADDITIVE}
    counters={k:Counter() for k in ("unknown_reasons","step_error_types","terminal_exceptions","stops")}
    for r in methods:
        for k in ("unknown_reasons","step_error_types","terminal_exceptions"):
            counters[k].update(r[k])
        counters["stops"][r["stop"]]+=1
    phase={}
    for k in ("wall_ms","batch_ms","index_ms","warmup_ms","teardown_ms"):
        seen=[b[k] for b in blocks if b[k] is not None]
        phase[k]=sum(seen) if seen else None
        phase[k+"_n"]=len(seen)
    return dict(n=len(methods),jvms=len(blocks),jvm_failed=sum(b["jvm_failed"] for b in blocks),
                verified=sum(b["verified"] for b in blocks),**sums,**phase,**counters)

def ratio(a,b):
    if not b:
        return 1.0 if not a else None
    return a/b

def comparison(blocks,ref_blocks):
    x,y=aggregate(blocks),aggregate(ref_blocks)
    assert x["n"] == y["n"] and x["jvms"] == y["jvms"]
    split={}
    for p in sorted({b["project"] for b in blocks}):
        split[p]=(aggregate([b for b in blocks if b["project"]==p]),
                  aggregate([b for b in ref_blocks if b["project"]==p]))
    violations={}
    def minimum(name,a,b,threshold):
        if b and a/b<threshold:
            violations[name]=threshold-a/b
    for k in ("paths","edges","steps"):
        minimum(k,x[k],y[k],.98)
    for p,(px,py) in split.items():
        minimum("paths:"+p,px["paths"],py["paths"],.95)
    c=(x["coverage"]-y["coverage"])/x["n"]
    if c< -1:
        violations["coverage_pp"]=(-1-c)/100
    for k in ("failed","jvm_failed","step_errors","solver_unknown","solver_timeouts"):
        if x[k]>y[k]:
            violations[k]=(x[k]-y[k])/x["n"]
    complete=not any(a["missing"] or a["unverified"] or a["verified"]!=a["jvms"] for a in (x,y))
    if not complete:
        violations["incomplete"]=1
    r=ratio(x["par2_ms"],y["par2_ms"])
    wall=ratio(x["wall_ms"] or 0,y["wall_ms"] or 0) if x["wall_ms_n"]==y["wall_ms_n"]==x["jvms"] else None
    result=dict(cost=r+(10+sum(violations.values()) if violations else 0), feasible=not violations,
       admissible=not violations and r<=.98 and wall is not None and wall<=1.02,
       complete=complete,par2_ratio=r,wall_ratio=wall,coverage_delta_pp=c,
       paths_ratio=ratio(x["paths"],y["paths"]),edges_ratio=ratio(x["edges"],y["edges"]),steps_ratio=ratio(x["steps"],y["steps"]),
       violations=violations,totals=x,reference=y,projects=split)
    return result
