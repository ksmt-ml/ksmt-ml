"""Isolated fixed-budget JVM runner derived from the primary A/A. The original file is unchanged."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import os
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent/"recovery/scripts"))
import base
import metrics
from recover import host_sample, manifest

WALL_CAP = 105
FIXED_CAP = 140
ACQUISITION_END = datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()
RELEASE = base.read(base.EXPECTED)
TRACE_FIELDS = ("USVM_CLIENT_STATS","LIVE141_ARM","USVM_SOLVER","USVM_TABLE_ITE","LIVE_SEARCH_OPTIONS",
                "SMT_TRACE","SMT_DELAY_US","usvm.jvm.api.jar.path","usvm.jvm.approximations.jar.path","LC_ALL","TZ","TMPDIR")

def measure(path, job, mode, arm, classpath, options=None, selector="FORK_DEPTH", steps=4000, runtime=None, delay_us=250,watchdog_s=None, extra_env=None):
    path.mkdir(parents=True)
    scratch=path/"scratch"
    scratch.mkdir()
    env=base.public_env(scratch)
    options = options or {}
    is_z3 = arm == "z3"
    env.update(USVM_CLIENT_STATS="1",LIVE141_ARM="z3" if is_z3 else "cvc5-default",
               USVM_SOLVER="z3" if is_z3 else "cvc5",USVM_TABLE_ITE="0",
               LIVE_SEARCH_OPTIONS=",".join(f"{k}={v}" for k,v in sorted(options.items())),
               SMT_TRACE=str(int(job["trace"])), SMT_DELAY_US=str(delay_us) if arm=="P" else "0",
               **{"usvm.jvm.api.jar.path":base.archive.CP_ENV["APIJAR"],
                  "usvm.jvm.approximations.jar.path":base.archive.CP_ENV["APPROXJAR"]})
    extra_env = extra_env or {}
    assert not extra_env.keys() & env.keys()
    env.update(extra_env)
    cp=os.pathsep.join([*([str(runtime)] if runtime else []),str(ROOT.parent/"followup/build/classes"),base.RUNTIME])
    cmd=["taskset","-c",str(job["core"]),*base.archive.jvm(scratch,base.HEAP),
         "-Dcvc5.skipLibraryLoad=true",f"-Dlive141.cvc5.jni={base.archive.CVC5_JNI}",
         "-cp",cp,"org.usvm.dataset.PowerDriverKt",f"out={path}",f"fixture={base.OLD/'build/livefixture.jar'}",
         f"seed={job['seed']}",f"cp={classpath}",f"methods={path/'methods.txt'}",f"budget={mode}",
         f"selector={selector}",f"steps={steps}"]
    (path/"methods.txt").write_text("\n".join(job["tags"])+"\n")
    base.write(path/"invocation.json",dict(cmd=cmd,env={k:env[k] for k in (*TRACE_FIELDS,*extra_env)},arm=arm,
             config=options,seed=job["seed"],core=job["core"],tags=job["tags"],mode=mode,selector=selector,steps=steps))
    try:
        cap=FIXED_CAP if mode=="fixed" else WALL_CAP
        return base.run_process(path,cmd,env,min(cap,watchdog_s) if watchdog_s is not None else cap)
    finally:
        shutil.rmtree(scratch)

def attest(path, job):
    invocation=base.read(path/"invocation.json")
    st=base.read(path/"status.json")
    raw=metrics.rows_from(path)
    assert invocation["seed"]==job["seed"] and invocation["core"]==job["core"]
    assert invocation["tags"]==(path/"methods.txt").read_text().splitlines()==job["tags"]
    assert list(raw)==job["tags"][:len(raw)], path
    if (path/"backend.json").is_file():
        base.validate_backend(path,base.read(path/"backend.json"),invocation,RELEASE)
    usvm=path/"usvm-options.txt"
    if usvm.is_file():
        text=usvm.read_text()
        for token in ("timeout=6s" if invocation["mode"]=="wall" else "timeout=Infinity",
                      f"stepLimit={invocation['steps']}",f"randomSeed={job['seed']}",invocation["selector"],
                      "throwExceptionOnStepFailure=false","solverTimeout=1.6s","typeOperationsTimeout=1s"):
            assert token in text,(path,token,text)
    opts=invocation["env"]
    for r in raw.values():
        s=r["stats"]
        assert s["sat"]+s["unsat"]+s["unknown"]==s["checks"]
        assert len(s["check_ms_list"])==s["checks"]
        if opts["SMT_DELAY_US"]=="0":
            assert s["injected_ms"]==0
        else:
            assert s["injected_ms"]+1e-7>=int(opts["SMT_DELAY_US"])/1000*s["checks"]
        assert s["trace_enabled"]==job["trace"]
        if job["trace"]:
            assert len(s["work_sha256"])==64 and len(s["check_prefix_sha256"])==min(512,s["checks"])
    obs=metrics.observe_block(path,job)
    obs.update(start=st["start_msk"],end=st["end_msk"],watchdog=st["watchdog"],
               returncode=st["returncode"],peak_rss_kib=st["peak_rss_kib"],digest=manifest(path))
    return obs

