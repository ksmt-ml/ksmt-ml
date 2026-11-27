"""Compare choices to the fixed array correction and audit equal-job budgets."""
from collections import Counter
from datetime import datetime
import random

from run import ROOT,base,cluster_ratio,score
from verify_live import percentile


def project_delta(rows,arm,baseline):
    by={}
    for test in rows:
        for j in test["rows"]:
            project=j["job"]["project"]
            d=by.setdefault(project,Counter())
            for label in (arm,baseline):
                arm_data=j["arms"][label]["cells"].values()
                d.update({label:sum(x["witnessed"]==x["expected"] for x in arm_data)})
            d["n"]+=len(j["job"]["tags"])
    families=sorted(by)
    if not families:
        return {}
    def delta(projects):
        return sum(by[p][arm]-by[p][baseline] for p in projects)/sum(by[p]["n"] for p in projects)
    rng=random.Random(2311202026)
    draws=sorted(delta(rng.choices(families,k=len(families))) for _ in range(10000))
    return dict(value=delta(families),ci=[percentile(draws,q) for q in (.025,.975)],by_project=by)


def failure_phases():
    phases=Counter()
    raw=[]
    for seal in sorted((ROOT/"raw").rglob("result.json")):
        if not seal.with_name("assignment.json").is_file():
            continue
        dataset=base.read(seal)
        for b in dataset["rows"]:
            for label,case in b["arms"].items():
                if not case["jvm_failed"]:
                    continue
                folder=seal.parent/b["job"]["id"]/label
                trace=folder/"events.tsv"
                last=trace.read_text().splitlines()[-1].split("\t")[0] if trace.is_file() and trace.stat().st_size else "no_event"
                status=case["state"]["watchdog"] or "exit_or_attestation"
                phase="|".join([dataset["split"],status,last])
                phases[phase]+=1
                raw.append(dict(path=str(folder.relative_to(ROOT)),phase=phase,state=case["state"]))
    return dict(counts=phases,failures=raw)


def anytime(data):
    horizons=(120,180,240)
    entries=[]
    descriptions=[(name,rec["search"],rec["train_start"],0.0) for name,rec in data["choices"].items()]
    repaired=ROOT.parent/"smac-recovery/evidence/summary.json"
    if repaired.is_file():
        z=base.read(repaired)
        descriptions.append(("r3-smac-recovery",z["search"],z["search"]["start"],z["charged_seconds"]))
    for label,search,start,spent in descriptions:
        begin=datetime.fromisoformat(start)
        tests=[]
        for r in search["records"]:
            result=r.get("result")
            if result is None:
                continue
            time=spent+(datetime.fromisoformat(r["end"])-begin).total_seconds()
            tests.append(dict(seconds=time,mask=r["mask"],gain=result["gain"],valid=result["feasible"]))
        accepted=[j for j in tests if j["valid"] and j["gain"]>=2]
        entries.append(dict(arm=label,attempts=tests,first_gain=min((q["seconds"] for q in accepted),default=None),
            gain_at={str(t):max((r["gain"] for r in tests if r["valid"] and r["seconds"]<=t),default=0) for t in horizons}))
    return entries


def main():
    summary=ROOT/"evidence/summary.json"
    if not summary.is_file():
        return
    data=base.read(summary)
    lock=datetime.fromisoformat(data["lock"])
    end=datetime.fromisoformat(data["end"])
    rows=[]
    for f in sorted((ROOT/"raw/test").glob("r*/result.json")):
        j=base.read(f)
        assert j["configs"]==data["options"] and j["split"]=="test"
        for run in j["rows"]:
            for arm in run["arms"].values():
                assert lock<=datetime.fromisoformat(arm["state"]["start_msk"])<=datetime.fromisoformat(arm["state"]["end_msk"])<=end
        rows.append(j)
    assert rows
    names=("complete_outcomes","known_outcomes","retrieved_outcomes","unknown","step_errors","solver_timeouts","wrong","jvm_failed")
    compare={}
    for a in data["options"]:
        for b in ("A","E"):
            if a==b:
                continue
            d=[score(r,a,b) for r in rows]
            same=[pair for r in d for pair in r["matching"]]
            counts={k:[sum(r["totals"][i][k] for r in d) for i in (0,1)] for k in names}
            compare[a+"/"+b]=dict(full_delta=project_delta(rows,a,b),times=cluster_ratio(same,"check"),
              matched=len(same),counts=counts,total=sum(r["totals"][0]["n"] for r in d),
              blocks=sum(len(r["rows"]) for r in rows), planned=sum(r["planned"] for r in rows))
    planning=[]
    for a,d in data["choices"].items():
        s=d["search"]
        v=d["validation"]
        train=(datetime.fromisoformat(s["end"])-datetime.fromisoformat(d["train_start"])).total_seconds()
        validation=(datetime.fromisoformat(v["end"])-datetime.fromisoformat(v["start"])).total_seconds()
        planning.append(dict(arm=a,train_s=train,validation_s=validation,model_s=s["inference_s"],
           train_budget_within=train<=271,validation_budget_within=validation<=141,
           evaluations=s["evaluations"],v_candidates=len(v["checked"])))
    base.write(ROOT/"evidence/comparison.json",dict(input_sha=base.sha(summary),at=base.now(),
         labels=data["labels"],comparisons=compare,budgets=planning,failures=failure_phases(),anytime=anytime(data)))


if __name__=="__main__":
    main()
