"""Calibrate deterministic solver resources, then verify repeated states at two load levels."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import importlib.util
from pathlib import Path
import os
import random
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base
from recover import manifest, host_sample

WALL=120
LIMITS=(500,2000,8000,32000,128000)
FAMILY_WIDTH={"bounded-product":8,"divide-remainder":8,"difference-squares":11,"split-carry":23}
CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()
ORACLE=ROOT.parent.parent/"campaign/.venv/bin/z3"

def generator():
    source=ROOT.parent/"bv-study/src/freeze.py"
    impl=importlib.util.spec_from_file_location("arithmetic_program",source)
    module=importlib.util.module_from_spec(impl)
    impl.loader.exec_module(module)
    return module

def arm_opts(label):
    units=int(label[1:]) if label.startswith("R") else 0
    return {"tlimit-per":str(WALL if units==0 else 800),"reproducible-resource-limit":str(units)}

def seal():
    out=ROOT/"inputs/manifest.json"
    if out.exists():
        return base.read(out)
    gen=generator()
    rng=random.Random(23070141)
    streams=ROOT/"inputs/streams"
    streams.mkdir(exist_ok=True)
    catalog={}
    for family,width in FAMILY_WIDTH.items():
        for idx in range(6):
            p=streams/(family+f"-b{idx+1:02d}.smt2")
            w=width+rng.randrange(4)
            words,answers,constants=gen.program(family,w,idx,rng)
            p.write_text(words)
            catalog[p.stem]=dict(id=p.stem,family=family,split="calibration" if idx<2 else "test",
                                expected=answers,sha256=base.sha(p),path=str(p.relative_to(ROOT)),width=w)
    jobs=[]
    test=[]
    for idx,(key,p) in enumerate(catalog.items()):
        if p["split"]=="calibration":
            labels=["W",*[f"R{r}" for r in LIMITS]]
            rng.shuffle(labels)
            jobs.append(dict(id=key,core=base.CPUS[idx%4],program=key,order=labels))
        else:
            for rep in range(4):
                order=["W","R"] if (rep+idx)%2==0 else ["R","W"]
                test.append(dict(id=key+f"-r{rep+1}",program=key,rep=rep+1,core=base.CPUS[(idx+rep)%4],order=order))
    rng.shuffle(jobs)
    rng.shuffle(test)
    data=dict(start=base.now(),protocol=base.sha(ROOT.parent/"protocols/07-budget.md"),programs=catalog,calibration=jobs,test=test,
              generator_sha=base.sha(ROOT.parent/"bv-study/src/freeze.py"))
    base.write(out,data)
    return data

def build(cpu="14"):
    evidence=ROOT/"evidence/build"
    source=ROOT/"src/NativeSequence.java"
    out=ROOT/"build/classes"
    if (evidence/"hashes.json").is_file():
        assert base.read(evidence/"hashes.json")==manifest(out)
        return out
    out.mkdir(exist_ok=True)
    evidence.mkdir(exist_ok=True)
    cmd=["taskset","-c",str(cpu),str(base.archive.JDK/"javac"),"-J-XX:ActiveProcessorCount=1",
          "-cp",str(base.archive.CVC5_JAR),"-d",str(out),str(source)]
    base.write(evidence/"invocation.json",dict(cmd=cmd,source=base.sha(source)))
    s=base.run_process(evidence,cmd,base.public_env(evidence),45)
    assert s["returncode"]==0 and not s["watchdog"],(s,(evidence/"stderr.log").read_text())
    base.write(evidence/"hashes.json",manifest(out))
    return out

def load_oracle(p,idx):
    out=ROOT/"raw/oracle"/p["id"]
    inp=ROOT/p["path"]
    assert base.sha(inp)==p["sha256"]
    if not out.exists():
        out.mkdir(parents=True)
        core=base.CPUS[idx%4]
        cmd=["taskset","-c",str(core),str(ORACLE),"-T:20","-smt2",str(inp)]
        base.write(out/"invocation.json",dict(cmd=cmd,input=p["sha256"]))
        base.run_process(out,cmd,base.public_env(out),23)
    st=base.read(out/"status.json")
    actual=(out/"stdout.log").read_text().splitlines()
    assert not any(a in ("sat","unsat") and a!=b for a,b in zip(actual,p["expected"])),(p["id"],actual,p["expected"])
    return bool(st["returncode"]==0 and not st["watchdog"] and actual==p["expected"])

def read_cell(out,p,opt):
    st=base.read(out/"status.json")
    facts={}
    cells=[]
    units={}
    done=False
    for text in (out/"stdout.log").read_text().splitlines():
        row=text.split("\t")
        if row[0]=="RUNTIME":
            assert row[1]=="1.4.1" and row[3]=="1"
        if row[0]=="OPTION":
            facts["/".join(row[1:3])]=row[3]
        if row[0]=="CHECK":
            _,idx,status,wall,cpu,reason=row
            if status.startswith("unknown (") and status.endswith(")"):
                status,reason="unknown",status.removeprefix("unknown (")[:-1]
            n=int(idx)
            assert n==len(cells)+1 and n<=len(p["expected"])
            cells.append(dict(idx=n,status=status,reason=reason,expected=p["expected"][n-1],
                              wall_ns=int(wall),cpu_ns=int(cpu)))
        if row[0]=="UNITS":
            units[int(row[1])]=int(row[2])
        if row[0]=="END":
            done=int(row[1])==len(cells)==len(p["expected"])
    if done and st["returncode"]==0 and not st["watchdog"]:
        for key,v in opt.items():
            assert facts["before/"+key]==facts["after/"+key]==v,(out,key,v,facts)
    else:
        done=False
    while len(cells)<len(p["expected"]):
        idx=len(cells)+1
        cells.append(dict(idx=idx,status="missing",reason="",expected=p["expected"][idx-1],wall_ns=0,cpu_ns=0))
    for c in cells:
        if c["idx"] in units:
            c["units_delta"]=units[c["idx"]]
    return dict(st=st,options=opt,settings=facts,complete=done,cells=cells,files=manifest(out))

def solve(out,core,program,label,built):
    opt=arm_opts(label)
    if out.exists() and not (out/"status.json").is_file():
        out.rename(out.with_name(out.name+f"-interrupted-{int(time.time())}"))
    if not out.exists():
        out.mkdir(parents=True)
        cmd=["taskset","-c",str(core),*base.archive.jvm(out,"512m"),"-Dcvc5.skipLibraryLoad=true",
             "-Dcvc5jni="+str(base.archive.CVC5_JNI),"-cp",os.pathsep.join(map(str,(built,base.archive.CVC5_JAR))),
             "NativeSequence",str(ROOT/program["path"]),*(k+"="+v for k,v in opt.items())]
        base.write(out/"invocation.json",dict(cmd=cmd,input_sha256=program["sha256"],core=core,arm=label))
        base.run_process(out,cmd,base.public_env(out),14)
    return read_cell(out,program,opt)

def scores(artifact):
    values=Counter()
    for rec in artifact:
        values["jvm_failed"]+=not rec["complete"]
        for c in rec["cells"]:
            values["n"]+=1
            values["correct"]+=c["status"]==c["expected"]
            values["incorrect"]+=c["status"] in ("sat","unsat") and c["status"]!=c["expected"]
            values["unknown"]+=c["status"]=="unknown"
            values["timeout"]+=c["status"]=="unknown" and "TIMEOUT" in c["reason"].upper()
            values["resourceout"]+=c["status"]=="unknown" and "RESOURCE" in c["reason"].upper()
            values["missing"]+=c["status"]=="missing"
            values["command_error"]+=c["status"] not in ("sat","unsat","unknown","missing")
            values["wall_ns"]+=c["wall_ns"]
            values["cpu_ns"]+=c["cpu_ns"]
    return values

def main():
    base.setup_limits()
    data=seal()
    assert data["protocol"]==base.sha(ROOT.parent/"protocols/07-budget.md")
    built=build()
    p=data["programs"]
    evidence=ROOT/"evidence"
    def oracle_lane(i):
        return {key:load_oracle(program,j) for j,(key,program) in enumerate(p.items()) if j%4==i}
    with ThreadPoolExecutor(4) as pool:
        ballots={k:v for partial in pool.map(oracle_lane,range(4)) for k,v in partial.items()}
    base.write(evidence/"oracle.json",ballots)
    assert sum(v for k,v in ballots.items() if p[k]["split"]=="test")>=12
    compatible={}
    for j in data["calibration"]:
        if not ballots[j["program"]]:
            continue
        assert time.time()+100<CUTOFF
        stage=ROOT/"raw/calibration"/j["id"]
        row={a:solve(stage/a,j["core"],p[j["program"]],a,built) for a in j["order"]}
        compatible[j["id"]]=row
        print("budget calibration",j["id"],base.now(),flush=True)
    opt_scores={a:scores([v[a] for v in compatible.values()]) for a in ("W",*[f"R{r}" for r in LIMITS])}
    good=[a for a,r in opt_scores.items() if a!="W" and not r["jvm_failed"] and not r["incorrect"]]
    assert good and not opt_scores["W"]["jvm_failed"],opt_scores
    w=opt_scores["W"]
    finalist=min(good,key=lambda a:(abs(opt_scores[a]["correct"]-w["correct"]),
                opt_scores[a]["incorrect"],abs(opt_scores[a]["wall_ns"]-w["wall_ns"])))
    lock=evidence/"test-lock.json"
    if lock.exists():
        assert base.read(lock)["choice"]==finalist
    else:
        base.write(lock,dict(at=base.now(),choice=finalist,calibration=opt_scores,
                             input_sha=base.sha(ROOT/"inputs/manifest.json"),host=host_sample()))
    pairs=evidence/"test"
    pairs.mkdir(exist_ok=True)
    def execute(j,stratum):
        if not ballots[j["program"]] or time.time()+37>=CUTOFF:
            return None
        out=ROOT/"raw"/stratum/j["id"]
        results={}
        for label in j["order"]:
            results[label]=solve(out/label,j["core"],p[j["program"]],finalist if label=="R" else "W",built)
        record=dict(job=j,stratum=stratum,results=results)
        dest=pairs/(stratum+"-"+j["id"]+".json")
        if dest.exists():
            assert base.read(dest)==record
        else:
            base.write(dest,record)
        return record
    observations=[]
    jobsets=[data["test"][i::4] for i in range(4)]
    for n,part in enumerate(jobsets):
        epochs=("single","parallel") if n%2==0 else ("parallel","single")
        for stratum in epochs:
            if stratum=="single":
                obs=[execute(j,stratum) for j in part]
            else:
                def lane(core):
                    return [execute(j,stratum) for j in part if j["core"]==core]
                with ThreadPoolExecutor(4) as pool:
                    obs=[r for g in pool.map(lane,base.CPUS) for r in g]
            observations.extend(x for x in obs if x)
            print("budget",n,stratum,base.now(),"pairs",len(observations),flush=True)
    groups=defaultdict(list)
    changes=defaultdict(Counter)
    all_times=[]
    for o in observations:
        for arm,r in o["results"].items():
            key=o["stratum"]+"/"+arm
            groups[key].append(r)
            all_times.append(r["st"])
    for name, vv in groups.items():
        events=defaultdict(list)
        js=[o["job"]["program"] for o in observations if o["stratum"]==name.split("/")[0]]
        for program,r in zip(js,vv,strict=True):
            for c in r["cells"]:
                events[(program,c["idx"])].append(c["status"])
        for key,vs in events.items():
            if len(vs)>1:
                changes[name]["repeated_queries"]+=1
                changes[name]["flipped_queries"]+=len(set(vs))>1
    base.write(evidence/"summary.json",dict(start=min((r["start_msk"] for r in all_times),default=base.now()),
          end=max((r["end_msk"] for r in all_times),default=base.now()),chosen=finalist,
          calibration=opt_scores,oracle=ballots,observations=len(observations),scores={k:scores(v) for k,v in groups.items()},
          repeatability=changes,host=host_sample()))

if __name__=="__main__":
    main()
