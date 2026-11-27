"""Reuse the witnessed live runner with a new exhaustive Java oracle."""
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
from pathlib import Path
import importlib.util
import os
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"recovery/scripts"))
sys.path.insert(0,str(TOP/"followup/src"))
import base
from recover import manifest
from analyze_power import cluster_ratio

ARMS={"A":{},"B":{},"bv-bool":{"bv-to-bool":"true"},
      "no-static":{"static-learning":"false"},"array-index":{"arrays-eager-index":"false"},
      "bitwise":{"bitwise-eq":"true"}}

def reuse():
    source=TOP/"oracle-live/src/run.py"
    impl=importlib.util.spec_from_file_location("witnessed_run",source)
    module=importlib.util.module_from_spec(impl)
    impl.loader.exec_module(module)
    return module

def compile_fixture(cpu=14):
    marker=ROOT/"evidence/build/runtime.json"
    build=ROOT/"build"
    jar=build/"kernels.jar"
    out=build/"driver"
    client=TOP/"followup/build/classes"
    if marker.is_file():
        r=base.read(marker)
        assert r["jar"]==base.sha(jar) and r["driver"]==manifest(out)
        return jar,out,client
    where=marker.parent
    where.mkdir(parents=True,exist_ok=True)
    classes=build/"java"
    scratch=build/"scratch"
    for p in (out,classes,scratch):
        p.mkdir(exist_ok=True)
    prefix=["taskset","-c",str(cpu)]
    stages={
      "javac":prefix+[str(base.archive.JDK/"javac"),"-J-XX:ActiveProcessorCount=1","-d",str(classes),
               *map(str,sorted((ROOT/"src/programs").glob("*.java")))],
      "jar":prefix+[str(base.archive.JDK/"jar"),"--create","--file",str(jar),"-C",str(classes),"."],
      "oracle":prefix+[*base.archive.jvm(scratch,"256m"),"-cp",str(jar),"examples.kernels.Oracle"],
    }
    deps=("kotlin-compiler-embeddable-2.1.0.jar","kotlin-stdlib-2.1.0.jar","kotlin-script-runtime-2.1.0.jar",
          "kotlin-reflect-1.6.10.jar","kotlin-daemon-embeddable-2.1.0.jar","annotations-13.0.jar",
          "trove4j-1.0.20200330.jar","kotlinx-coroutines-core-jvm-1.6.4.jar")
    comp=os.pathsep.join(str(next(base.archive.MODULES.rglob(n))) for n in deps)
    friends=[base.BUILD/"overlay",base.OLD/"build/client",client]
    stages["driver"]=prefix+[*base.archive.jvm(scratch,"1g"),"-cp",comp,"org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
       "-no-stdlib","-no-reflect","-jvm-target","1.8","-classpath",str(client)+os.pathsep+base.RUNTIME,
       "-Xfriend-paths="+",".join(map(str,friends)),"-d",str(out),str(ROOT/"src/LiveOracle.kt")]
    for label,cmd in stages.items():
        dst=where/label
        dst.mkdir(exist_ok=True)
        if not (dst/"status.json").exists():
            base.write(dst/"invocation.json",cmd)
            base.run_process(dst,cmd,base.public_env(scratch),105)
        st=base.read(dst/"status.json")
        assert st["returncode"]==0 and not st["watchdog"],(st,dst,(dst/"stderr.log").read_text())
    witness=(where/"oracle/stdout.log").read_text()
    assert len(witness.splitlines())==24
    base.write(marker,dict(jar=base.sha(jar),driver=manifest(out),concrete=witness,
                           sources=manifest(ROOT/"src/programs")))
    return jar,out,client

def contracts():
    ready=ROOT/"evidence/certified.json"
    if ready.is_file():
        return base.read(ready)
    expected=base.read(base.EXPECTED)
    distinct=[a for a in ARMS if a!="B"]
    def lane(core):
        report={}
        for idx,a in enumerate(distinct):
            if base.CPUS[idx%4]!=core:
                continue
            root=ROOT/"raw/contracts"/a
            if not root.exists():
                base.case(root,core,"default",ARMS[a],0,mode="contract",cap=48)
            st=base.read(root/"status.json")
            try:
                assert st["returncode"]==0 and not st["watchdog"]
                assert "CONTRACT_PASS" in (root/"stdout.log").read_text()
                base.validate_backend(root,base.read(root/"backend.json"),base.read(root/"invocation.json"),expected)
                ok=True
            except (AssertionError,FileNotFoundError) as error:
                ok=False
            report[a]=dict(status=st,ok=ok,digests=manifest(root))
        return report
    with ThreadPoolExecutor(4) as executors:
        out={a:v for r in executors.map(lane,base.CPUS) for a,v in r.items()}
    assert out["A"]["ok"]
    base.write(ready,out)
    return out

def freeze_dataset(jar,passed):
    out=ROOT/"inputs/schedule.json"
    if out.is_file():
        return base.read(out)
    by={}
    expected={}
    witness=base.read(ROOT/"evidence/build/runtime.json")["concrete"].splitlines()
    for line in witness:
        family,cls,w,k,categories=line.split("\t")
        fs=by.setdefault(family,{})
        tag=f"kernel/examples.kernels.{cls}.run(II)I"
        fs[cls]=tag
        expected[tag]=list(map(int,categories.split(",")))
    rng=random.Random(23111105)
    schedules=[]
    for j,(family,tags) in enumerate(by.items()):
        arms=list(passed)
        rng.shuffle(arms)
        preserve=rng.sample(range(len(arms)),k=min(4,len(arms)))
        if len(preserve)<4:
            preserve=(preserve*4)[:4]
        names=list(tags.values())
        for rep,idx in enumerate(preserve):
            order=names[:]
            rng.shuffle(order)
            schedules.append(dict(id=f"{family}-r{rep+1}",project=family,core=base.CPUS[(j+rep)%4],seed=(0,71)[rep%2],
                            tags=order,expected=[], order=arms[idx:]+arms[:idx]))
    rng.shuffle(schedules)
    data=dict(at=base.now(),protocol=base.sha(TOP/"protocols/11-known-kernels.md"),fixture=base.sha(jar),
              arms=passed,cases=by,expected=expected,schedule=schedules)
    base.write(out,data)
    return data

def main():
    base.setup_limits()
    module=reuse()
    build=compile_fixture()
    supported=contracts()
    options={a:ARMS[a] for a in ARMS if a=="B" or supported[a]["ok"]}
    frozen=freeze_dataset(build[0],options)
    assert frozen["arms"]==options and frozen["protocol"]==base.sha(TOP/"protocols/11-known-kernels.md")
    orig=module.audit
    def audit(where,job):
        data=orig(where,job)
        for q in data["cells"].values():
            q["expected"]=frozen["expected"][q["tag"]]
        return data
    module.ROOT=ROOT
    module.ARMS=options
    module.CASES=frozen["cases"]
    module.prep=lambda:build
    module.freeze=lambda jar:frozen
    module.audit=audit
    # Reuse the driver without introducing any test-dependent model tool or training exporter.
    module.base=base
    root_protocol=TOP/"protocols/11-known-kernels.md"
    module.require_preregistered=lambda expected: expected==base.sha(root_protocol)
    module.main()
    evidence=ROOT/"evidence"
    summary=base.read(evidence/"summary.json")
    selected=default_bundles(options)
    summary["paired_ratios"]={a:aggregate_pairs(v) for a,v in selected.items()}
    base.write(evidence/"summary.json",summary)

def default_bundles(options):
    paired={a:[] for a in options if a!="A"}
    for f in sorted((ROOT/"evidence").glob("*-r*.json")):
        r=base.read(f)
        for a in paired:
            x,y=(r["runs"][k] for k in (a,"A"))
            for tag,m in x["cells"].items():
                b=y["cells"][tag]
                equal=(m["witnessed"]==b["witnessed"]==b["expected"] and not any(
                         z[k] for z in (m,b) for k in ("wrong","step_errors","solver_unknown","witness_errors")) and
                       all(m[k]==b[k] for k in ("paths","edges","steps","checks","coverage")))
                paired[a].append(dict(project=r["job"]["project"],program=tag,exact=equal,
                  costs=[m["par2_ms"],b["par2_ms"]],check=[m["check_ms"],b["check_ms"]],
                  paths=[m["paths"],b["paths"]]))
    return paired

def aggregate_pairs(rows):
    eq=[r for r in rows if r["exact"]]
    return dict(n=len(rows),work_equal=len(eq),
          all={k:cluster_ratio(rows,k) for k in ("costs","check","paths")},
          equal={k:cluster_ratio(eq,k) for k in ("costs","check","paths")})

if __name__=="__main__":
    main()
