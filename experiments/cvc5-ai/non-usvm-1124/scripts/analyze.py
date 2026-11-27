"""Summarize paired producer panels and independent-family uncertainty."""
from collections import Counter, defaultdict
from math import isfinite
from pathlib import Path
import random
import statistics as stat

from common import ROOT, now, ordered, read, sha, write

BOOT=10_000
ARM_METHODS=("default","fixed","smac","sol","random")
HW_SEQ={"phase-counter","gray-counter","modular-counter","serializer","history-ring","pulse-stretcher",
        "elastic-pipe","fair-arbiter","window-voter"}


def mean(values):
    seq=list(values)
    return stat.fmean(seq) if seq else None


def cluster(rows):
    per=defaultdict(list)
    for r in rows:
        per[r["family"]].append(r)
    return per


def aggregate(rows):
    rows=list(rows)
    family=cluster(rows)
    measures=Counter(r["status"] for r in rows)
    return dict(par2=mean(r["par2"] for r in rows),cluster_par2=mean(mean(r["par2"] for r in v) for v in family.values()),
                n=len(rows),cases=len({r['id'] for r in rows}),families=len(family),
                checks=sum(r["verified_checks"] for r in rows),statuses=dict(measures),
                first=min((r["start"] for r in rows),default=None),last=max((r["stop"] for r in rows),default=None))


def screens(where):
    rows=read(where/"rows.json")
    groups=defaultdict(list)
    for r in rows:
        groups[r["arm"]].append(r)
    arms={a:aggregate(group) for a,group in groups.items()}
    pairs=defaultdict(dict)
    for r in rows:
        if r["arm"] in {"0","AA"}:
            pairs[r["id"],r["rep"]][r["arm"]]=r
    d=[abs(v["0"]["seconds"]-v["AA"]["seconds"]) for v in pairs.values() if len(v)==2
       and all(r["status"]=="solved" for r in v.values())]
    relative=[abs(v["0"]["seconds"]-v["AA"]["seconds"])/max(.001,(v["0"]["seconds"]+v["AA"]["seconds"])/2)
              for v in pairs.values() if len(v)==2 and all(r["status"]=="solved" for r in v.values())]
    rows_by_case=defaultdict(list)
    for r in rows:
        rows_by_case[r["id"]].append(r)
    by_family=defaultdict(list)
    for r in rows:
        by_family[r["family"]].append(r)
    return dict(path=str(where.relative_to(ROOT)),arms=arms,sha256=sha(where/"rows.json"),
                identical_pairs=len(d),median_aa_s=stat.median(d) if d else None,
                q90_aa_s=sorted(d)[int(.9*(len(d)-1))] if d else None,
                median_aa_rel=stat.median(relative) if relative else None,
                floor=min((r["seconds"] for r in rows if r["status"]=="solved"),default=None),
                all_timed_out=sorted(k for k, group in rows_by_case.items() if all(r["status"]=="timeout" for r in group)),
                by_family={k:{a:aggregate(r for r in v if r["arm"]==a) for a in groups} for k,v in sorted(by_family.items())})


def browser_time(seconds):
    from datetime import datetime
    from common import MSK
    return datetime.fromtimestamp(seconds,MSK).isoformat(timespec="milliseconds")


def search_history(plan):
    raw=ROOT/"raw"/plan["name"]
    rounds=[]
    for seed in plan["seeds"]:
        here=raw/f"s{seed}"
        if not (here/"schedule.json").is_file():
            continue
        when=read(here/"schedule.json")
        span=dict(seed=seed,start=browser_time(when["start"]),deadline=browser_time(when["end"]),methods={})
        for method in ("default","smac","sol","random"):
            loc=here/method
            history=read(loc/"trials.json") if (loc/"trials.json").exists() else []
            summary=read(loc/"search.json") if (loc/"search.json").exists() else {}
            calls=read(loc/"calls.json") if (loc/"calls.json").exists() else []
            native=list((loc/"native").rglob("runhistory.json"))
            eligible=sorted((r for r in history if r["valid"]),key=lambda r:(r["par2"],r["id"]))
            trajectory={}
            for minute in (2,5,10,15,20,plan["budget"]//60):
                stop=browser_time(when["start"]+minute*60)
                possible=[r for r in eligible if r["stop"]<=stop]
                trajectory[str(minute)]=possible[0]["par2"] if possible else None
            span["methods"][method]=dict(seen=len(history),completed=bool(summary),anytime=trajectory,
                status=summary.get("status","running"),actual_start=summary.get("observed_start",(history or [{}])[0].get("start")),
                actual_stop=summary.get("stop"),cpu=when["cpus"][method],
                best=eligible[0] if eligible else None,baseline=(history or [None])[0],
                invalid=sum(not x["valid"] for x in history),repeated_baseline=len(list((loc/"repeat").glob("*/aggregate.json"))),
                model_calls=len(calls),model_success=sum(c["status"]=="ok" for c in calls),
                unique_model_threads=len({c["thread"] for c in calls if c["thread"]}),
                model_seconds=sum(c["seconds"] for c in calls),
                model_flags={k:v for k,v in Counter(c["status"] for c in calls).items()},
                native_evidence=[str(x.relative_to(ROOT)) for x in native])
        rounds.append(span)
    return rounds


def paired(left,right,seed):
    keys=sorted(set(left)&set(right))
    a=[left[k] for k in keys]
    b=[right[k] for k in keys]
    if not keys:
        return None
    rng=random.Random(seed)
    delta=[]
    ratio=[]
    for _ in range(BOOT):
        indices=rng.choices(range(len(keys)),k=len(keys))
        m=mean(a[i] for i in indices)
        n=mean(b[i] for i in indices)
        delta.append(m-n)
        ratio.append(m/n if n else None)
    delta.sort()
    ratio=[r for r in ratio if r is not None]
    ratio.sort()
    interval=lambda values:[values[int(.025*(len(values)-1))],values[int(.975*(len(values)-1))]] if values else None
    loo={}
    for i,key in enumerate(keys):
        va=a[:i]+a[i+1:]
        vb=b[:i]+b[i+1:]
        if va and mean(vb):
            loo[key]=mean(va)/mean(vb)
    return dict(n=len(keys),estimate=mean(a)-mean(b),relative=mean(a)/mean(b),delta_ci=interval(delta),ratio_ci=interval(ratio),
                leave_one_out_ratio=loo,
                better=sum(x<y for x,y in zip(a,b)),worse=sum(x>y for x,y in zip(a,b)),
                families={k:[left[k],right[k]] for k in keys})


def test_subset(mapped,rows,locked,include):
    rows=[r for r in rows if include(r["family"])]
    actual=defaultdict(list)
    for r in rows:
        actual[r["arm"]].append(r)
    mapped_stats={key:aggregate(actual[target]) for key,target in mapped.items()}
    per=defaultdict(dict)
    for key,target in mapped.items():
        per[key]={family:mean(r["par2"] for r in rs) for family,rs in cluster(actual[target]).items()}
    method_arms={"default":["default"],"fixed":["fixed"]}
    for method in ARM_METHODS[2:]:
        method_arms[method]=sorted(k for k in mapped if k.startswith(method+"-"))
    by_method={}
    for method,arms in method_arms.items():
        all_grouped=defaultdict(list)
        for arm in arms:
            for family,value in per[arm].items():
                all_grouped[family].append(value)
        by_method[method]={k:mean(v) for k,v in all_grouped.items()}
    contrasts={}
    for a,b in [(m,"default") for m in ARM_METHODS[1:]]+[("sol","smac"),("sol","random"),("smac","random")]:
        contrasts[f"{a}/{b}"]=paired(by_method[a],by_method[b],697011)
    solve_changes={}
    base_rows={(r["id"],r["rep"]):r["status"]=="solved" for r in actual[mapped["default"]]}
    for method,arms in method_arms.items():
        gains=[]
        losses=[]
        for arm in arms:
            selected={(r["id"],r["rep"]):r["status"]=="solved" for r in actual[mapped[arm]]}
            assert set(selected)==set(base_rows)
            gains.append(sum(selected[k] and not base_rows[k] for k in selected))
            losses.append(sum(base_rows[k] and not selected[k] for k in selected))
        solve_changes[method]={"gains":gains,"losses":losses}
    return dict(methods={m:dict(cluster_par2=mean(v.values()),
                               solved_per_seed=[mapped_stats[x]["statuses"].get("solved",0) for x in method_arms[m]],
                               checks_per_seed=[mapped_stats[x]["checks"] for x in method_arms[m]],
                               changes=solve_changes[m]) for m,v in by_method.items()},
                contrasts=contrasts,by_config=mapped_stats,cohort=aggregate(actual[mapped["default"]]),
                actual_configs=len(actual),known_disagreements=sorted({r["id"] for r in rows if r["status"]=="wrong"}))


def measured_test(plan):
    base=ROOT/"raw"/plan["name"]
    f=base/"test"/"score-map.json"
    if not f.exists():
        return None
    info=read(f)
    rows=read(f.with_name("rows.json"))
    mapped=info["configuration_aliases"]
    lock_f=ROOT/"evidence"/f"{plan['name']}-lock.json"
    locked=read(lock_f)
    result=dict(hashes=dict(lock=sha(lock_f),rows=sha(f.with_name("rows.json"))),info=info,selection=locked["selected"],
                overall=test_subset(mapped,rows,locked,lambda _:True))
    if plan["producer"]=="hw":
        result["sequential"]=test_subset(mapped,rows,locked,lambda f:f in HW_SEQ)
        result["combinational"]=test_subset(mapped,rows,locked,lambda f:f not in HW_SEQ)
    return result


def read_phases():
    rows=[]
    for f in sorted((ROOT/"raw/phases").glob("*.start")):
        base=f.with_suffix("")
        stop=base.with_suffix(".stop")
        c=base.with_suffix(".command")
        x=stop.read_text().split() if stop.exists() else []
        rows.append(dict(name=base.name,start=f.read_text().strip(),stop=x[0] if x else None,code=int(x[1]) if x else None,
                         command=c.read_text().strip() if c.exists() else None,path=str(base.relative_to(ROOT))))
    return rows


def analyze():
    data=dict(at=now(),panels={},oracles={},search={},test={},secondary={},phases=read_phases())
    for p in sorted((ROOT/"raw").glob("screen-*")):
        if (p/"rows.json").is_file():
            data["panels"][p.name]=screens(p)
    for p in sorted((ROOT/"raw").glob("oracle-*")):
        if (p/"rows.json").is_file():
            data["oracles"][p.name]=aggregate(read(p/"rows.json"))
    for f in sorted((ROOT/"protocols").glob("*-search.json")):
        p=read(f)
        data["search"][p["name"]]=dict(plan=p,plan_file=str(f.relative_to(ROOT)),rounds=search_history(p))
        done=measured_test(p)
        if done:
            data["test"][p["name"]]=done
    for key in ("public-hw","public-opt","why3-transfer","why3-live","floor-replication","live-replay-test",
                "horizon-transfer","public-hw-cap45","public-idl-cap30"):
        path=ROOT/"raw"/key/"summary.json"
        if path.exists():
            data["secondary"][key]=read(path)
    witness=ROOT/"raw/karatsuba-exhaustive/summary.json"
    if witness.exists():
        data["exhaustive_oracle"]=read(witness)
    protected=ROOT/"raw/arbiter-exhaustive/summary.json"
    if protected.exists():
        data["arbiter_oracle"]=read(protected)
    published=ROOT/"raw/public-hw-oracle110/summary.json"
    if published.exists():
        data["public_hw_oracle"]=read(published)
    if "floor-replication" in data["secondary"]:
        replicas=read(ROOT/"raw/floor-replication/rows.json")
        mapped=read(ROOT/"raw/opt-search/test/score-map.json")["configuration_aliases"]
        corresponding=test_subset(mapped,replicas,None,lambda _:True)
        data["secondary"]["floor-replication"]["matched"]=corresponding["contrasts"]
    if "horizon-transfer" in data["secondary"]:
        native=read(ROOT/"raw/horizon-transfer/cvc5/rows.json")
        readings={arm:{k:mean(r["par2"] for r in rs) for k,rs in cluster(x for x in native if x["arm"]==arm).items()}
                  for arm in ("0","fixed")}
        data["secondary"]["horizon-transfer"]["matched"]=paired(readings["fixed"],readings["0"],697012)
    data["wire_audits"]={}
    for location in sorted((ROOT/"raw").glob("hw-wire-*")):
        final=location/"rows.json"
        if final.exists():
            rows=read(final)
            data["wire_audits"][location.name]=dict(n=len(rows),same_commands=sum(r.get("wire_dump_same_commands",False) for r in rows),
                matched_statuses=sum(r.get("same_answers",False) for r in rows),native_timeout=sum(r["live_timeout"] for r in rows),
                witnesses=sum(bool(r.get("witness",{}).get("ok")) for r in rows))
    for filename in ("disk-usage.json","hw-base-witness-audit.json","calibration.json","artifact-audit.json","hw-gate.json","oracle-certificate-audit.json"):
        p=ROOT/"evidence"/filename
        if p.exists():
            keep=read(p)
            keep.pop("rows",None)
            data[filename[:-5]]=keep
    write(ROOT/"evidence/analysis.json",data)
    return data


if __name__=="__main__":
    d=analyze()
    print(d["at"],sorted(d["panels"]),sorted(d["test"]))
