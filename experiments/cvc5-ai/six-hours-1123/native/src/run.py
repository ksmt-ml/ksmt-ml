"""Run the sealed SMT-LIB streams in Java JNI and check exact responses independently."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import os
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base
from recover import manifest, host_sample
from freeze import OPTIONS

CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()
EXPECTED=base.read(base.EXPECTED)
Z3=ROOT.parent.parent/"campaign/.venv/bin/z3"
BASELINE={
    **base.DEFAULTS,"bv-to-bool":"false","incremental":"true","produce-models":"true","produce-unsat-cores":"true",
    "seed":"0","tlimit-per":"1600",
}

def compile_java():
    where=ROOT/"evidence/build"
    out=ROOT/"build/classes"
    if (where/"sha256.json").exists():
        assert base.read(where/"sha256.json")==manifest(out)
        return out
    where.mkdir(parents=True,exist_ok=True)
    out.mkdir(parents=True,exist_ok=True)
    source=ROOT/"src/NativeSequence.java"
    cmd=["taskset","-c","14",str(base.archive.JDK/"javac"),"-J-XX:ActiveProcessorCount=1",
         "-cp",str(base.archive.CVC5_JAR),"-d",str(out),str(source)]
    base.write(where/"invocation.json",dict(args=cmd,source=base.sha(source)))
    state=base.run_process(where,cmd,base.public_env(where),50)
    assert state["returncode"]==0 and not state["watchdog"],(state,(where/"stderr.log").read_text())
    base.write(where/"sha256.json",manifest(out))
    return out

def run_solver(where,core,arm,path,out):
    where.mkdir(parents=True)
    user=where/"scratch"
    user.mkdir()
    vm=base.archive.jvm(user,"512m")
    cmd=["taskset","-c",str(core),*vm,"-Dcvc5.skipLibraryLoad=true","-Dcvc5jni="+str(base.archive.CVC5_JNI),
         "-cp",str(out)+os.pathsep+str(base.archive.CVC5_JAR),"NativeSequence",str(path),
         *(k+"="+v for k,v in OPTIONS[arm].items())]
    base.write(where/"invocation.json",dict(cmd=cmd,arm=arm,core=core,input=base.sha(path)))
    try:
        base.run_process(where,cmd,base.public_env(user),35)
    finally:
        shutil.rmtree(user)

def observe(where,program):
    s=base.read(where/"status.json")
    inv=base.read(where/"invocation.json")
    facts={}
    rows=[]
    mapped=set()
    cap=program["expected"]
    end=None
    expected_options={**BASELINE,**OPTIONS[inv["arm"]]}
    for line in (where/"stdout.log").read_text().splitlines():
        row=line.split("\t")
        if row[0]=="RUNTIME":
            assert row[1]=="1.4.1" and row[2].startswith("21.") and row[3]=="1",row
        elif row[0]=="OPTION":
            _,phase,key,value=row
            facts[phase,key]=value
        elif row[0]=="MAP":
            mapped.add(row[1].split(maxsplit=5)[-1])
        elif row[0]=="END":
            end=int(row[1])
        elif row[0]=="CHECK":
            _,n,status,wall,cpu,reason=row
            assert int(n)==len(rows)+1 and float(wall)>=0 and float(cpu)>=0
            index=int(n)-1
            if status.startswith("unknown (") and status.endswith(")"):
                reason=status[len("unknown ("):-1]
                status="unknown"
            assert status in ("sat","unsat","unknown") or status.startswith("(error"),(where,row)
            rows.append(dict(index=index+1,status=status,expected=cap[index],wall_ns=int(wall),cpu_ns=int(cpu),reason=reason))
        elif line.startswith("Cpus_allowed_list:"):
            assert int(line.partition(":")[2])==inv["core"],(line,inv)
    clean=s["returncode"]==0 and not s["watchdog"] and end==len(cap)==len(rows)
    if clean:
        assert mapped=={str(base.archive.CVC5_JNI)},mapped
        for phase in ("before","after"):
            for option,value in expected_options.items():
                assert facts[phase,option]==value,(where,phase,option,facts[phase,option],value)
    return dict(arm=inv["arm"],complete=clean,checks=rows,options={f"{p}/{k}":v for (p,k),v in facts.items()},
                state=s,digest=manifest(where))

def oracle_case(where,p,core):
    path=ROOT/p["path"]
    assert p["sha256"]==base.sha(path)
    if where.exists() and not (where/"status.json").exists():
        where.rename(where.with_name(where.name+f"-interrupted-{int(time.time())}"))
    if not where.exists():
        where.mkdir(parents=True)
        cmd=["taskset","-c",str(core),str(Z3),"-T:24","-smt2",str(path)]
        base.write(where/"invocation.json",dict(cmd=cmd,input=p["sha256"]))
        base.run_process(where,cmd,base.public_env(where),27)
    st=base.read(where/"status.json")
    got=(where/"stdout.log").read_text().splitlines()
    fatal=(st["returncode"] is None or bool(st["watchdog"]) or st["returncode"]!=0)
    contradictory=any(t in ("sat","unsat") and t!=ref for t,ref in zip(got,p["expected"]))
    if contradictory:
        raise ValueError((where,got,p["expected"]))
    return dict(id=p["id"],verified=not fatal and got==p["expected"],got=got,status=st,digest=manifest(where))

def main():
    base.setup_limits()
    assert base.sha(base.archive.CVC5_JNI)==EXPECTED["cvc5_jni_sha256"]
    assert base.sha(base.archive.CVC5_JAR)==EXPECTED["cvc5_jar_sha256"]
    data=base.read(ROOT/"inputs/manifest.json")
    assert base.sha(ROOT.parent/"protocols/03-native-theories.md")==data["sha_protocol"]
    assert data["option_sets"]==OPTIONS
    out=compile_java()
    programs={p["id"]:p for p in data["programs"]}
    for p in programs.values():
        assert base.sha(ROOT/p["path"])==p["sha256"]
    evidence=ROOT/"evidence"
    raw=ROOT/"raw"
    launch=evidence/"launch.json"
    if not launch.exists():
        base.write(launch,dict(at=base.now(),host=host_sample(),manifest=base.sha(ROOT/"inputs/manifest.json"),
                               z3_sha256=base.sha(Z3)))
    def oracle_lane(core):
        return [oracle_case(raw/"oracle"/p["id"],p,core)
                for i,p in enumerate(programs.values()) if 12+i%4==core and time.time()+34<CUTOFF]
    with ThreadPoolExecutor(4) as pool:
        oracles=[r for records in pool.map(oracle_lane,base.CPUS) for r in records]
    base.write(evidence/"oracle.json",oracles)
    print("oracle",sum(r["verified"] for r in oracles),len(oracles),base.now(),flush=True)
    assert len(oracles)==len(programs) and sum(x["verified"] for x in oracles)>=24
    oracle_ok={r["id"] for r in oracles if r["verified"]}

    contract=ROOT/"inputs/contract.smt2"
    if not contract.exists():
        contract.write_text("(set-logic QF_BV)\n(declare-fun x () (_ BitVec 8))\n(assert (= x #x2a))\n"
                            "(check-sat)\n(push 1)\n(assert (distinct x #x2a))\n(check-sat)\n(pop 1)\n")
    checker=dict(expected=["sat","unsat"])
    gate={}
    for arm in OPTIONS:
        where=raw/"contract"/arm
        if not where.exists():
            run_solver(where,14,arm,contract,out)
        row=observe(where,checker)
        gate[arm]=dict(complete=row["complete"],passed=row["complete"] and
             ["sat","unsat"]==[x["status"] for x in row["checks"]],data=row)
    assert gate["default"]["passed"],gate["default"]
    base.write(evidence/"contract.json",gate)
    admitted={a for a,r in gate.items() if r["passed"]}
    pairs=evidence/"pairs"
    pairs.mkdir(exist_ok=True)
    def lane(core):
        for j in data["jobs"]:
            if j["core"]!=core or j["program"] not in oracle_ok:
                continue
            where=raw/"paired"/j["id"]
            sealed=pairs/f"{j['id']}.json"
            if sealed.exists():
                old=base.read(sealed)
                for a,row in old["arms"].items():
                    assert row["digest"]==manifest(where/a),where/a
                continue
            if time.time()+len(admitted)*40>=CUTOFF:
                return
            prior_complete=where.exists() and all((where/a/"status.json").is_file() for a in admitted)
            if where.exists() and not prior_complete:
                renamed=raw/"interrupted"
                renamed.mkdir(exist_ok=True)
                where.rename(renamed/f"{j['id']}-{int(time.time())}")
            base.ensure_disk()
            p=programs[j["program"]]
            arms={}
            for arm in j["order"]:
                if arm not in admitted:
                    continue
                if not prior_complete:
                    run_solver(where/arm,core,arm,ROOT/p["path"],out)
                arms[arm]=observe(where/arm,p)
            base.write(sealed,dict(job=j,arms=arms))
            print("native",j["id"],base.now(),len(arms),flush=True)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lane,base.CPUS))
    base.write(evidence/"end.json",dict(at=base.now(),host=host_sample(),paired_jobs=len(list(pairs.glob("*.json")))))

if __name__=="__main__":
    main()
