"""Run the isolated client-encoding intervention with concrete Java outcome witnesses."""
from pathlib import Path
import importlib.util
import os
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"recovery/scripts"))
import base
from recover import manifest

ARMS={"cvc5":{},"prototype":{},"z3":{}}
CASES={
 "float": {"raw":"ieee/examples.ieee.Ieee32.fromFloatBits(F)I","direct":"ieee/examples.ieee.Ieee32.direct(F)I"},
 "double":{"raw":"ieee/examples.ieee.Ieee64.fromDoubleBits(D)I","direct":"ieee/examples.ieee.Ieee64.direct(D)I"},
 "negation":{"raw":"ieee/examples.ieee.Scaled32.rawNegated(F)I","direct":"ieee/examples.ieee.Scaled32.directNegated(F)I"},
 "scaled":{"raw":"ieee/examples.ieee.Scaled64.rawScaled(D)I","direct":"ieee/examples.ieee.Scaled64.directScaled(D)I"},
 "payload":{"quiet32":"ieee/examples.ieee.Payload.quiet32(I)I","quiet64":"ieee/examples.ieee.Payload.quiet64(I)I"},
}

def load_witness_runner():
    src=TOP/"oracle-live/src/run.py"
    spec=importlib.util.spec_from_file_location("encoding_witness",src)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def compile_overlay(cpu=14):
    built=ROOT/"build"
    evidence=ROOT/"evidence/build"
    marker=evidence/"runtime.json"
    front=TOP/"followup/build/classes"
    jar=built/"encoding-fixture.jar"
    driver=built/"driver"
    if marker.exists():
        d=base.read(marker)
        assert d["driver"]==manifest(driver) and d["jar"]==base.sha(jar)
        return jar,driver,front
    for p in (built/"classes",driver,built/"temp",evidence):
        p.mkdir(parents=True,exist_ok=True)
    temp=built/"temp"
    libs=("kotlin-compiler-embeddable-2.1.0.jar","kotlin-stdlib-2.1.0.jar","kotlin-script-runtime-2.1.0.jar",
          "kotlin-reflect-1.6.10.jar","kotlin-daemon-embeddable-2.1.0.jar","trove4j-1.0.20200330.jar",
          "kotlinx-coroutines-core-jvm-1.6.4.jar","annotations-13.0.jar")
    kk=os.pathsep.join(str(next(base.archive.MODULES.rglob(n))) for n in libs)
    friends=[base.BUILD/"overlay",base.OLD/"build/client",front,
             *[p for p in base.archive.LOCAL_CP if p.is_relative_to(base.archive.ORIGINAL) and p.exists()]]
    prefix=["taskset","-c",str(cpu)]
    procedures={
      "javac":prefix+[str(base.archive.JDK/"javac"),"-J-XX:ActiveProcessorCount=1","-d",str(built/"classes"),
                      *map(str,sorted((ROOT/"src/classes").glob("*.java")))],
      "jar":prefix+[str(base.archive.JDK/"jar"),"--create","--file",str(jar),"-C",str(built/"classes"),"."],
      "concrete":prefix+[*base.archive.jvm(temp,"256m"),"-cp",str(jar),"examples.ieee.Proof"],
      "driver":prefix+[*base.archive.jvm(temp,"1g"),"-cp",kk,"org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
        "-no-stdlib","-no-reflect","-jvm-target","1.8","-classpath",str(front)+os.pathsep+base.RUNTIME,
        "-Xfriend-paths="+",".join(map(str,friends)),"-d",str(driver),
        str(ROOT/"src/JcApproximations.kt"),str(ROOT/"src/LiveOracle.kt")],
    }
    for name,cmd in procedures.items():
        loc=evidence/name
        loc.mkdir(exist_ok=True)
        if not (loc/"status.json").exists():
            base.write(loc/"invocation.json",dict(cmd=cmd))
            base.run_process(loc,cmd,base.public_env(temp),150)
        st=base.read(loc/"status.json")
        assert st["returncode"]==0 and not st["watchdog"],(loc,st,(loc/"stderr.log").read_text())
    concrete=(evidence/"concrete/stdout.log").read_text()
    assert concrete.splitlines()==["category-control 1,2,3,4,5,6,7","payload-control 1"]
    base.write(marker,dict(jar=base.sha(jar),driver=manifest(driver),source=manifest(ROOT/"src"),concrete=concrete))
    return jar,driver,front

def freeze(jar):
    path=ROOT/"inputs/schedule.json"
    if path.exists():
        return base.read(path)
    rng=random.Random(23111301)
    schedule=[]
    for i,(p,targets) in enumerate(CASES.items()):
        order=list(ARMS)
        rng.shuffle(order)
        classes=list(targets.values())
        for r in range(4):
            params=classes[:]
            rng.shuffle(params)
            k=r%3
            schedule.append(dict(id=f"{p}-r{r+1}",project=p,core=base.CPUS[(i+r)%4],seed=(0,71)[r%2],
                          tags=params,expected=[1] if p=="payload" else list(range(1,8)),order=order[k:]+order[:k]))
    rng.shuffle(schedule)
    data=dict(at=base.now(),protocol=base.sha(TOP/"protocols/13-fp-encoding.md"),fixture=base.sha(jar),
           cases=CASES,arms=ARMS,schedule=schedule)
    base.write(path,data)
    return data

def main():
    base.setup_limits()
    module=load_witness_runner()
    built=compile_overlay()
    p=freeze(built[0])
    orig=module.acquire
    def invoke(where,job,arm,bundle):
        local={"SMT_TEST_FP_INVERSE":"1" if arm=="prototype" else "0"}
        return orig(where,job,arm,bundle,extra_env=local)
    module.ROOT=ROOT
    module.ARMS=ARMS
    module.CASES=CASES
    module.prep=lambda:built
    module.freeze=lambda jar:p
    module.require_preregistered=lambda expected:expected==base.sha(TOP/"protocols/13-fp-encoding.md")
    module.acquire=invoke
    module.main()

if __name__=="__main__":
    main()
