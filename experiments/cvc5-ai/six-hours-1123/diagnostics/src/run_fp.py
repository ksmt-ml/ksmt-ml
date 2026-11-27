"""Execute and independently score the predeclared KSMT/IEEE controls."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import json
import os
import random
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base
from recover import manifest, host_sample

ARMS = {
    "default": {},
    "arrays-exp": {"arrays-exp":"true"},
    "bv-eager-eval": {"bv-eager-eval":"true"},
    "no-simplification": {"simplification":"none"},
    "no-static-learning": {"static-learning":"false"},
    "z3": {},
}
CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()

def compile_driver():
    output=ROOT/"evidence/fp-build"
    out=ROOT/"build/fp"
    if (output/"runtime-sha256.json").exists():
        assert base.read(output/"runtime-sha256.json")==manifest(out)
        return out
    output.mkdir(parents=True)
    out.mkdir(parents=True,exist_ok=True)
    scratch=ROOT/"build/tmp"
    scratch.mkdir(exist_ok=True)
    deps=("kotlin-compiler-embeddable-2.1.0.jar","kotlin-stdlib-2.1.0.jar",
          "kotlin-script-runtime-2.1.0.jar","kotlin-reflect-1.6.10.jar","kotlin-daemon-embeddable-2.1.0.jar",
          "trove4j-1.0.20200330.jar","kotlinx-coroutines-core-jvm-1.6.4.jar","annotations-13.0.jar")
    cc=os.pathsep.join(str(next(base.archive.MODULES.rglob(n))) for n in deps)
    friends=','.join(map(str,[base.BUILD/"overlay",base.OLD/"build/client"]))
    source=ROOT/"src/FpAdapter.kt"
    cmd=["taskset","-c","14",*base.archive.jvm(scratch,"1g"),"-cp",cc,
         "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler","-no-stdlib","-no-reflect","-jvm-target","1.8",
         "-classpath",base.RUNTIME,"-Xfriend-paths="+friends,"-d",str(out),str(source)]
    base.write(output/"invocation.json",dict(cmd=cmd,source_sha=base.sha(source)))
    st=base.run_process(output,cmd,base.public_env(scratch),110)
    assert st["returncode"]==0, (st,(output/"stderr.log").read_text())
    base.write(output/"runtime-sha256.json",manifest(out))
    return out

def run_case(path, cpu, arm, out):
    path.mkdir(parents=True)
    scratch=path/"scratch"
    scratch.mkdir()
    env=base.public_env(scratch)
    env.update(LIVE141_ARM="z3" if arm=="z3" else "cvc5-default",USVM_CLIENT_STATS="1",
          LIVE_SEARCH_OPTIONS=",".join(f"{k}={v}" for k,v in sorted(ARMS[arm].items())))
    cmd=["taskset","-c",str(cpu),*base.archive.jvm(scratch,"800m"),
         "-Dcvc5.skipLibraryLoad=true",f"-Dlive141.cvc5.jni={base.archive.CVC5_JNI}",
         "-cp",str(out)+os.pathsep+base.RUNTIME,"org.usvm.dataset.FpAdapterKt",str(path)]
    inv=dict(arm=arm,core=cpu,config=ARMS[arm],cmd=cmd,
             env={k:env[k] for k in ("LIVE141_ARM","USVM_CLIENT_STATS","LIVE_SEARCH_OPTIONS")})
    base.write(path/"invocation.json",inv)
    try:
        base.run_process(path,cmd,env,28)
    finally:
        shutil.rmtree(scratch)

def attest(path):
    st=base.read(path/"status.json")
    inv=base.read(path/"invocation.json")
    out=[]
    if st["returncode"]==0 and not st["watchdog"]:
        base.validate_backend(path,base.read(path/"backend.json"),inv,base.read(base.EXPECTED))
        out=[json.loads(x) for x in (path/"results.jsonl").read_text().splitlines()]
        assert len(out)==52 and len({tuple(r[k] for k in ("mode","case","width","expected")) for r in out})==52
    for r in out:
        if r["status"]=="unsupported":
            assert "fp.to_ieee_bv" in r["detail"]
    return dict(status=st,arm=inv["arm"],verified=bool(out),results=out,files=manifest(path))

def main():
    base.setup_limits()
    root=ROOT/"raw/fp"
    root.mkdir(exist_ok=True)
    seal=ROOT/"inputs/fp-schedule.json"
    if not seal.exists():
        order=[(i,a) for i in range(3) for a in ARMS]
        random.Random(2302).shuffle(order)
        base.write(seal,dict(at=base.now(),version=1,option_sets=ARMS,
            protocol_sha=base.sha(ROOT.parent/"protocols/02-fp-adapter.md"),
            schedule=[dict(id=f"{a}-r{i+1}",arm=a,core=12+j%4) for j,(i,a) in enumerate(order)]))
    seal_data=base.read(seal)
    assert seal_data["protocol_sha"]==base.sha(ROOT.parent/"protocols/02-fp-adapter.md")
    compiled=compile_driver()
    start=host_sample()
    deadline=min(time.time()+480,CUTOFF)
    def lane(core):
        local=[]
        for item in seal_data["schedule"]:
            if item["core"]!=core or time.time()+40>=deadline:
                continue
            path=root/item["id"]
            if path.exists() and not (path/"status.json").is_file():
                path.rename(root/f"{item['id']}-interrupted-{int(time.time())}")
            if not path.exists():
                base.ensure_disk()
                run_case(path,core,item["arm"],compiled)
            data=attest(path)
            print("fp",item["id"],data["status"]["returncode"],base.now(),flush=True)
            local.append(dict(id=item["id"],**data))
        return local
    with ThreadPoolExecutor(4) as pool:
        runs=[r for rs in pool.map(lane,base.CPUS) for r in rs]
    summary={}
    for a in ARMS:
        summary[a]={}
        for mode in ("direct","inverse"):
            c=Counter()
            for r in (x for x in runs if x["arm"]==a):
                if not r["verified"]:
                    c["jvm_failure"]+=1
                for cell in (x for x in r["results"] if x["mode"]==mode):
                    c[cell["status"]]+=1
                    if cell["status"] in ("SAT","UNSAT") and (cell["status"]!=cell["expected"] or
                              cell["status"]=="SAT" and cell["model_correct"] is not True):
                        c["incorrect"]+=1
            summary[a][mode]=c
    base.write(ROOT/"evidence/fp.json",dict(start=min((r["status"]["start_msk"] for r in runs),default=base.now()),
                     end=max((r["status"]["end_msk"] for r in runs),default=base.now()),seal_sha=base.sha(seal),
                     before=start,after=host_sample(),runs=runs,summary=summary))

if __name__=="__main__":
    main()
