"""Independently check persisted native cells and aggregate matched costs."""
from collections import Counter, defaultdict
from pathlib import Path
import math
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base
from verify_live import percentile
from run import observe

FIELDS=("par2_ms","cpu_known_ms","wall_known_ms","wall_total_ms","unknown","solver_timeouts","incorrect",
        "missing","command_error","jvm_error","correct","sat_correct","unsat_correct","n")

def interval(rows,field,unit):
    if not rows:
        return dict(value=None,ci=[None,None],clusters=0)
    classes=sorted({r[unit] for r in rows})
    numbers={c:[sum(r[field][i] for r in rows if r[unit]==c) for i in (0,1)] for c in classes}
    def ratio(parts):
        a=sum(numbers[c][0] for c in parts)
        b=sum(numbers[c][1] for c in parts)
        return a/b if b>0 else None
    rng=random.Random(23031126)
    values=sorted(x for x in [ratio(rng.choices(classes,k=len(classes))) for _ in range(5000)] if x is not None)
    return dict(value=ratio(classes),ci=[percentile(values,q) for q in (.025,.975)],
               clusters=len(classes),by_cluster={c:ratio([c]) for c in classes})

def as_cells(measured,expected):
    seen={s["index"]:s for s in measured["checks"]}
    out=[]
    for n,expect in enumerate(expected,1):
        this=seen.get(n)
        if not this:
            this=dict(index=n,status="missing",expected=expect,wall_ns=0,cpu_ns=0,reason="")
        assert this["expected"]==expect
        out.append(this)
    return out

def compare(pair, programs, arm):
    cells=[]
    for rec in pair:
        j=rec["job"]
        p=programs[j["program"]]
        lhs,rhs=[rec["arms"][a] for a in (arm,"default")]
        a,b=[as_cells(x,p["expected"]) for x in (lhs,rhs)]
        row={k:[0,0] for k in FIELDS}
        row.update(program=p["id"],family=p["family"],job=j["id"])
        for i,n in enumerate((lhs,rhs)):
            row["jvm_error"][i]=not n["complete"]
        for aa,bb in zip(a,b,strict=True):
            known=aa["status"]==bb["status"]==aa["expected"]
            assert aa["expected"]==bb["expected"]
            for i,r in enumerate((aa,bb)):
                wall=r["wall_ns"]/1e6
                cpu=r["cpu_ns"]/1e6
                correct=r["status"]==r["expected"]
                row["n"][i]+=1
                row["wall_total_ms"][i]+=wall
                row["par2_ms"][i]+=wall if correct else 3200
                if known:
                    row["cpu_known_ms"][i]+=cpu
                    row["wall_known_ms"][i]+=wall
                if correct:
                    row["correct"][i]+=1
                    row[r["status"]+"_correct"][i]+=1
                elif r["status"] in ("sat","unsat"):
                    row["incorrect"][i]+=1
                elif r["status"]=="unknown":
                    row["unknown"][i]+=1
                    row["solver_timeouts"][i]+=int("timeout" in r["reason"].lower())
                elif r["status"]=="missing":
                    row["missing"][i]+=1
                else:
                    row["command_error"][i]+=1
        cells.append(row)
    return cells

def main():
    data=base.read(ROOT/"inputs/manifest.json")
    programs={p["id"]:p for p in data["programs"]}
    wall_records=[]
    oracle=base.read(ROOT/"evidence/oracle.json")
    archive=ROOT/"raw"
    for r in oracle:
        p=programs[r["id"]]
        assert base.sha(ROOT/p["path"])==p["sha256"]
        if r["verified"]:
            assert (archive/"oracle"/r["id"]/"stdout.log").read_text().splitlines()==p["expected"]
    checked=[]
    for f in sorted((ROOT/"evidence/pairs").glob("*.json")):
        x=base.read(f)
        j=x["job"]
        assert j in data["jobs"]
        for a,xprime in x["arms"].items():
            path=archive/"paired"/j["id"]/a
            recomputed=observe(path,programs[j["program"]])
            assert xprime==recomputed,(j["id"],a)
            assert base.read(path/"invocation.json")["input"]==programs[j["program"]]["sha256"]
            wall_records.append(recomputed["state"])
        checked.append(x)
    if not checked:
        return
    gate=base.read(ROOT/"evidence/contract.json")
    results={}
    for a in data["option_sets"]:
        if not gate[a]["passed"] or a=="default":
            continue
        rows=compare(checked,programs,a)
        summary=[]
        for group in [None,*sorted({r["family"] for r in rows})]:
            subset=[r for r in rows if group is None or r["family"]==group]
            summary.append(dict(family=group,blocks=len(subset),programs=len({r["program"] for r in subset}),
                totals={k:[sum(x[k][i] for x in subset) for i in (0,1)] for k in FIELDS},
                ratios={k:interval(subset,k,"family" if group is None else "program")
                        for k in ("par2_ms","cpu_known_ms","wall_known_ms")}))
        results[a]=summary
    base.write(ROOT/"evidence/summary.json",dict(start=min(x["start_msk"] for x in wall_records),
          end=max(x["end_msk"] for x in wall_records),jvms=len(wall_records),blocks=len(checked),planned=len(data["jobs"]),
          oracle_passed=sum(r["verified"] for r in oracle),oracle_total=len(oracle),
          option_sets=data["option_sets"],admitted=[a for a,r in gate.items() if r["passed"]],results=results))

if __name__=="__main__":
    main()
