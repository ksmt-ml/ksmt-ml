"""Re-read paired files, aggregate by project and quantify A/A and positive control."""
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
import hashlib
import itertools
import json
import math
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base
import metrics
from verify_live import percentile
from run_power import attest

STATS_EQUAL = ("steps","completed_paths","returned_paths","branch_edges","coverage_pct","checks",
               "assert_calls","asserted_exprs","assume_exprs","sat","unsat","unknown",
               "unknown_reasons","stop_reason","type_unsat_rounds","exception_types",
               "pushes","pops","model_calls","unsat_core_calls")

def cluster_ratio(rows, field, *, seed=231126):
    projects=sorted({x["project"] for x in rows})
    if len(projects)<2:
        return dict(clusters=len(projects),value=None,ci=[None,None])
    totals={p:[sum(x[field][j] for x in rows if x["project"]==p) for j in (0,1)] for p in projects}
    def ratio(ps):
        a=sum(totals[p][0] for p in ps)
        b=sum(totals[p][1] for p in ps)
        return a/b if b else None
    rng=random.Random(seed)
    samples=sorted(v for v in [ratio(rng.choices(projects,k=len(projects))) for _ in range(10000)] if v is not None)
    return dict(value=ratio(projects),ci=[percentile(samples,q) for q in (.025,.975)],
                projects={p:ratio([p]) for p in projects},clusters=len(projects))

def power_selected(rows, field):
    projects=sorted({x["project"] for x in rows})
    vals=[]
    for p in projects:
        a,b=[sum(x[field][j] for x in rows if x["project"]==p) for j in (0,1)]
        if a>0 and b>0:
            vals.append(math.log(a/b))
    if len(vals)<3:
        return None
    center=mean(vals)
    residual=[v-center for v in vals]
    n=len(vals)
    patterns=list(itertools.product((-1,1),repeat=n))
    def pval(d):
        obs=sum(d)
        return sum(sum(v*s for v,s in zip(d,signs))<=obs+1e-12 for signs in patterns)/len(patterns)
    rng=random.Random(382311)
    draws=[rng.choices(residual,k=n) for _ in range(2500)]
    power={}
    for loss in [0, .01,.02,.03,.05,.075,.1,.125,.15,.175,.2,.25,.3,.4,.5]:
        effect=math.log1p(-loss)
        power[f"{loss:g}"]=sum(pval([x+effect for x in d])<=.05 for d in draws)/len(draws)
    return dict(p_values="one-sided exact sign permutation",n=n,project_aa_log=vals,
                power=power,minimum_at_80pct=next((float(k) for k,v in power.items() if v>=.8),None))

def trial(where, job):
    return (attest(where,job),metrics.rows_from(where))

def compare(jobs, mode, arm, trace, collected, ref_mode=None, estimate_sensitivity=True):
    ref_mode=ref_mode or mode
    units=[]
    labels=(arm,"A") if mode==ref_mode else (mode,ref_mode)
    totals={label:Counter() for label in labels}
    stops={label:Counter() for label in labels}
    errors={label:Counter() for label in labels}
    for j in jobs:
        if j["trace"]!=trace:
            continue
        av,bv=(collected[j["id"]][f"{mode}-{arm}"],collected[j["id"]][f"{ref_mode}-A"])
        for idx,tag in enumerate(j["tags"]):
            entry=dict(project=j["project"],job=j["id"],tag=tag)
            ar,br=[x.get(tag) for _,x in (av,bv)]
            for k in ("par2_ms","steps","paths","edges","checks","check_ms","coverage","failed",
                      "missing","solver_timeouts","other_unknown","step_errors"):
                entry[k]=[point["methods"][idx][k] for point,_ in (av,bv)]
            same=bool(ar and br and ar["status"]==br["status"]=="ok"
                      and all(ar["stats"][k]==br["stats"][k] for k in STATS_EQUAL))
            entry["aggregate_equivalent"]=same
            entry["identical_trace"]=bool(same and trace and ar["stats"]["work_sha256"]==br["stats"]["work_sha256"])
            entry["common_check_prefix"]=0
            entry["prefix_check_ms"]=[0,0]
            if trace and ar and br:
                s,t=(x["stats"] for x in (ar,br))
                for l,r in zip(s["check_prefix_sha256"],t["check_prefix_sha256"]):
                    if l!=r:
                        break
                    entry["common_check_prefix"]+=1
                n=entry["common_check_prefix"]
                entry["prefix_check_ms"]=[sum(x["check_ms_list"][:n]) for x in (s,t)]
            entry["analyze_ms"]=[x["analyze_ms"] if x else 0 for x in (ar,br)]
            entry["wall_ms"]=[x["wall_ms"] if x else 0 for x in (ar,br)]
            entry["injected_ms"]=ar["stats"]["injected_ms"] if ar else 0
            entry["clean"]= not any(entry[k][i] for k in ("failed","missing","step_errors","solver_timeouts","other_unknown")
                                     for i in (0,1))
            for label,(parsed,_),i in ((labels[0],av,0),(labels[1],bv,1)):
                totals[label].update({k:entry[k][i] for k in ("par2_ms","steps","paths","edges","checks","check_ms",
                                    "coverage","failed","missing","solver_timeouts","other_unknown","step_errors",
                                    "analyze_ms","wall_ms")})
                row=parsed["methods"][idx]
                stops[label].update([row["stop"]])
                errors[label].update(row["step_error_types"])
            units.append(entry)
    paired=[x for x in units if x["aggregate_equivalent"] and x["clean"]]
    exact=[x for x in paired if x["identical_trace"]]
    prefixes=[x for x in units if x["common_check_prefix"]]
    with_delay=[x for x in (exact if trace else paired) if x["injected_ms"]>0]
    dose_rows=[{**x,"dose_delta":[x["check_ms"][0]-x["check_ms"][1],x["injected_ms"]]}
               for x in with_delay]
    dose_interval=cluster_ratio(dose_rows,"dose_delta")
    def dose(rows):
        return sum(x["check_ms"][0]-x["check_ms"][1] for x in rows)
    ratio={v:cluster_ratio(units,v) for v in ("par2_ms","analyze_ms","check_ms","steps","paths","edges")}
    return dict(n=len(units),aggregate_equivalent=sum(x["aggregate_equivalent"] for x in units),
                identical_trace=sum(x["identical_trace"] for x in units),clean_equivalent=len(paired),
                exact_clean=len(exact),common_prefix_cells=len(prefixes),
                checks_on_common_prefix=sum(x["common_check_prefix"] for x in units),
                compared_checks=sum(min(x["checks"]) for x in units),
                ratio=ratio,same_work_ratio={k:cluster_ratio(paired,k) for k in ("check_ms","analyze_ms","wall_ms")},
                prefix_ratio=cluster_ratio(prefixes,"prefix_check_ms"),
                observed_delay_recovery=dose(with_delay)/sum(x["injected_ms"] for x in with_delay) if with_delay else None,
                dose_interval=dose_interval,injected_ms=sum(x["injected_ms"] for x in with_delay),delay_pairs=len(with_delay),
                sensitivity={k:power_selected(units,k) for k in ("par2_ms","analyze_ms","check_ms")} if estimate_sensitivity and arm=="B" and not trace else None,
                totals={k:dict(v) for k,v in totals.items()},stops={k:dict(v) for k,v in stops.items()},
                errors={k:dict(v) for k,v in errors.items()},cells=units)

def main():
    source=base.read(ROOT/"inputs/power.json")
    evidence=ROOT/"evidence/power"
    raw=ROOT/"raw/power"
    available=[j for j in source["jobs"] if (evidence/f'{j["id"]}.json').is_file()]
    data={}
    for j in available:
        out={}
        prior=base.read(evidence/f'{j["id"]}.json')
        assert prior["job"]==j
        for mode,arm in j["order"]:
            name=f"{mode}-{arm}"
            out[name]=trial(raw/j["id"]/name,j)
            assert out[name][0]==prior["arms"][name],(j["id"],name)
        data[j["id"]]=out
    rows=[v[0] for js in data.values() for v in js.values()]
    if not rows:
        return
    result=dict(start=min(r["start"] for r in rows),end=max(r["end"] for r in rows),available=len(available),
                planned=len(source["jobs"]),jvms=len(rows),jvm_failures=sum(r["jvm_failed"] for r in rows),
                projects=sorted({j["project"] for j in available}), comparisons={})
    for mode, arm, trace in itertools.product(("wall","fixed"),("B","P"),(False,True)):
        key=f"{mode}-{arm}-{'trace' if trace else 'plain'}"
        result["comparisons"][key]=compare(available,mode,arm,trace,data)
    result["cross_budget"]={("trace" if trace else "plain"):compare(available,"fixed","A",trace,data,ref_mode="wall")
                            for trace in (False,True)}
    base.write(ROOT/"evidence/power-summary.json",result)
    print(result["start"],result["end"],"paired blocks",len(available),"JVM",len(rows),flush=True)

if __name__=="__main__":
    main()
