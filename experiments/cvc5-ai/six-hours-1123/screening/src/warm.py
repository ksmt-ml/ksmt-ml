"""Matched analysis within one indexed JVM, one solver per method and arm."""
from datetime import datetime
from pathlib import Path
import json
import os
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base
import metrics
from historical import step_logs
from recover import manifest

CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()
NATIVE={**base.DEFAULTS,"bv-to-bool":"false","incremental":"true","produce-models":"true","produce-unsat-cores":"true","seed":"0"}
CVC5_RELEASE=base.read(base.EXPECTED)

def compile_driver(variant=None):
    where=ROOT/"evidence"/("warm-build" if variant is None else f"warm-build-{variant}")
    compiled=ROOT/"build"/("classes" if variant is None else f"classes-{variant}")
    if (where/"runtime-sha256.json").is_file():
        assert base.read(where/"runtime-sha256.json")==manifest(compiled)
        return compiled
    where.mkdir(parents=True,exist_ok=True)
    compiled.mkdir(parents=True,exist_ok=True)
    scratch=ROOT/"build/tmp"
    scratch.mkdir(exist_ok=True)
    deps=("kotlin-compiler-embeddable-2.1.0.jar","kotlin-stdlib-2.1.0.jar","kotlin-script-runtime-2.1.0.jar",
          "kotlin-reflect-1.6.10.jar","kotlin-daemon-embeddable-2.1.0.jar","trove4j-1.0.20200330.jar",
          "kotlinx-coroutines-core-jvm-1.6.4.jar","annotations-13.0.jar")
    compiler=os.pathsep.join(str(next(base.archive.MODULES.rglob(n))) for n in deps)
    trace=ROOT.parent/"followup/build/classes"
    classpath=str(trace)+os.pathsep+base.RUNTIME
    friends=[base.BUILD/"overlay",base.OLD/"build/client",trace]
    sources=sorted((ROOT/"build"/("src" if variant is None else f"src-{variant}")).glob("*.kt"))
    cmd=["taskset","-c","14",*base.archive.jvm(scratch,"1g"),"-cp",compiler,
        "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler","-no-stdlib","-no-reflect","-jvm-target","1.8",
        "-classpath",classpath,"-Xfriend-paths="+",".join(map(str,friends)),"-d",str(compiled),*map(str,sources)]
    base.write(where/"invocation.json",dict(cmd=cmd,src={str(s):base.sha(s) for s in sources}))
    st=base.run_process(where,cmd,base.public_env(scratch),120)
    assert st["returncode"]==0 and not st["watchdog"],(st,(where/"stderr.log").read_text())
    base.write(where/"runtime-sha256.json",manifest(compiled))
    return compiled

def option_text(opts):
    return ",".join(f"{k}={v}" for k,v in sorted(opts.items()))

def run_batch(where,job,project,compiled,cap=135,heap=base.HEAP,rss_limit=base.RSS_KIB):
    where.mkdir(parents=True)
    scratch=where/"scratch"
    scratch.mkdir()
    env=base.public_env(scratch)
    env.update(USVM_CLIENT_STATS="1",LIVE141_ARM="cvc5-default",USVM_SOLVER="cvc5",USVM_TABLE_ITE="0",
           LIVE_SEARCH_OPTIONS="",SMT_TRACE="1" if job["trace"] else "0",SMT_DELAY_US="0",
           **{"usvm.jvm.api.jar.path":base.archive.CP_ENV["APIJAR"],
              "usvm.jvm.approximations.jar.path":base.archive.CP_ENV["APPROXJAR"]})
    for record in project["files"]:
        assert base.sha(record["path"])==record["sha256"]
    project_cp=os.pathsep.join(r["path"] for r in project["files"])
    trace=ROOT.parent/"followup/build/classes"
    runtime=os.pathsep.join((str(compiled),str(trace),base.RUNTIME))
    cmd=["taskset","-c",str(job["core"]),*base.archive.jvm(scratch,heap),
        "-Dcvc5.skipLibraryLoad=true",f"-Dlive141.cvc5.jni={base.archive.CVC5_JNI}",
        "-cp",runtime,"org.usvm.dataset.WarmDriverKt",f"out={where}",f"fixture={base.OLD/'build/livefixture.jar'}",
        f"seed={job['seed']}",f"cp={project_cp}",f"workload={where/'job.tsv'}",
        f"steps={job['steps']}",f"budget={job['budget']}"]
    base.write(where/"invocation.json",dict(cmd=cmd,arm="default",config={},job=job,core=job["core"],
        env={k:env[k] for k in ("LIVE141_ARM","USVM_CLIENT_STATS","LIVE_SEARCH_OPTIONS","SMT_TRACE","SMT_DELAY_US",
                            "USVM_SOLVER","USVM_TABLE_ITE")}))
    payload="".join(f"{r['id']}\t{r['tag']}\t{r['arm']}\t{r['selector']}\t{option_text(r['opts'])}\n" for r in job["cells"])
    (where/"job.tsv").write_text(payload)
    try:
        st=base.run_process(where,cmd,env,cap,rss_limit=rss_limit)
    finally:
        shutil.rmtree(scratch)
    return st

def observe(where,job):
    inv=base.read(where/"invocation.json")
    assert inv["job"]==job and inv["core"]==job["core"]
    assert inv["env"]["SMT_TRACE"]==str(int(job["trace"]))
    if (where/"backend.json").is_file():
        base.validate_backend(where,base.read(where/"backend.json"),inv,CVC5_RELEASE)
    elif base.read(where/"status.json")["returncode"]==0:
        raise ValueError(f"Missing backend certificate {where}")
    rows={}
    fname=where/"results.jsonl"
    if fname.is_file():
        for ln in fname.read_text().splitlines():
            try:
                x=json.loads(ln)
            except ValueError:
                assert base.read(where/"status.json")["watchdog"],(where,ln[-120:])
                continue
            assert x["id"] not in rows
            rows[x["id"]]=x
    assert list(rows)==[r["id"] for r in job["cells"][:len(rows)]]
    errors=step_logs((where/"stdout.log").read_text(errors="replace"))
    states=base.read(where/"status.json")
    result=[]
    for cell in job["cells"]:
        raw=rows.get(cell["id"])
        stats=raw["stats"] if raw else {}
        expected=NATIVE|cell["opts"]
        if raw:
            assert (raw["tag"],raw["option_label"],raw["selector"])==(cell["tag"],cell["arm"],cell["selector"])
            assert stats["sat"]+stats["unsat"]+stats["unknown"]==stats["checks"],(where,cell["id"])
            assert stats["trace_enabled"]==job["trace"]
            if job["trace"]:
                assert sum(stats["theory_checks"].values())==stats["checks"]
                assert len(stats["check_prefix_sha256"])==min(stats["checks"],512)
            assert stats["injected_ms"]==0
            for phase,options in raw["native"].items():
                if options:
                    for k,v in expected.items():
                        assert options[k]==v,(where,cell,k,v,options[k])
                    if phase=="after":
                        assert options["tlimit-per"]=="1600"
            if stats["checks"]>0:
                assert all(raw["native"].values()),(where,cell)
        counts=errors.get(cell["id"],{})
        drops=sum(counts.values())
        timeout=stats.get("stop_reason")=="method_timeout"
        unknown=stats.get("unknown",0)
        native_timed_out=sum(v for k,v in stats.get("unknown_reasons",{}).items() if metrics.TIMEOUT.search(k))
        failed=not raw or raw["status"]!="ok" or timeout or drops>0 or unknown>0
        result.append(dict(id=cell["id"],arm=cell["arm"],selector=cell["selector"],tag=cell["tag"],
              par2_ms=16000 if failed else raw["wall_ms"],analyze_ms=raw["analyze_ms"] if raw else 0,
              wall_ms=raw["wall_ms"] if raw else 0,steps=stats.get("steps",0),
              paths=stats.get("completed_paths",0),edges=len(stats.get("branch_edges",[])),
              coverage=stats.get("coverage_pct",0),checks=stats.get("checks",0),check_ms=stats.get("check_ms",0),
              failed=bool(failed),step_errors=drops,error_types=counts,unknown=unknown,solver_timeouts=native_timed_out,
              wall_timeout=timeout,missing=not raw,status=raw["status"] if raw else "missing",
              stats=stats,native=raw["native"] if raw else {}))
    return dict(job=job,state=states,records=result,verified=(where/"backend.json").is_file(),
                complete=(states["returncode"]==0 and not states["watchdog"] and len(rows)==len(job["cells"])),
                hashes=manifest(where))
