"""Precommit independent real library methods using static ASM evidence."""
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import json
import os
import random
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"recovery/scripts"))
import base

MAVEN=Path.home()/".m2/repository"
COORDS={
    "automaton":("dk/brics/automaton/automaton/1.11-8",["dk.brics.automaton."],[]),
    "re2j":("com/google/re2j/re2j/1.6",["com.google.re2j."],[]),
    "RoaringBitmap":("org/roaringbitmap/RoaringBitmap/1.0.0",["org.roaringbitmap."],[]),
    "fastutil":("it/unimi/dsi/fastutil/8.5.12",["it.unimi.dsi.fastutil.ints.","it.unimi.dsi.fastutil.longs."],[]),
    "guava":("com/google/guava/guava/33.4.8-jre",["com.google.common.math.","com.google.common.primitives."],[]),
    "commons-math3":("org/apache/commons/commons-math3/3.6.1",["org.apache.commons.math3.util.",
                         "org.apache.commons.math3.special.","org.apache.commons.math3.complex.",
                         "org.apache.commons.math3.analysis.function."],[]),
    "caffeine":("com/github/ben-manes/caffeine/caffeine/3.2.0",["com.github.benmanes.caffeine.cache."],[]),
    "HdrHistogram":("org/hdrhistogram/HdrHistogram/2.2.2",["org.HdrHistogram."],[]),
    "lz4-java":("org/lz4/lz4-java/1.8.0",["net.jpountz.lz4.","net.jpountz.xxhash."],[]),
    "commons-pool2":("org/apache/commons/commons-pool2/2.12.0",["org.apache.commons.pool2."],[]),
    "commons-exec":("org/apache/commons/commons-exec/1.6.0",["org.apache.commons.exec."],[]),
    "commons-cli":("commons-cli/commons-cli/1.9.0",["org.apache.commons.cli."],[]),
    "commons-text":("org/apache/commons/commons-text/1.14.0",["org.apache.commons.text.similarity.",
                    "org.apache.commons.text.numbers.","org.apache.commons.text.lookup."],
                    ["org/apache/commons/commons-lang3/3.18.0"]),
    "commons-io":("commons-io/commons-io/2.17.0",["org.apache.commons.io.ByteOrderMark","org.apache.commons.io.EndianUtils",
                      "org.apache.commons.io.IOUtils","org.apache.commons.io.output.ByteArrayOutputStream","org.apache.commons.io.input.BoundedInputStream"],[]),
    "jackson-core":("com/fasterxml/jackson/core/jackson-core/2.18.4",["com.fasterxml.jackson.core.io.Number",
                     "com.fasterxml.jackson.core.io.CharTypes","com.fasterxml.jackson.core.util."],[]),
    "gson":("com/google/code/gson/gson/2.13.2",["com.google.gson.internal.","com.google.gson.stream."],[]),
    "antlr":("org/antlr/antlr4-runtime/4.13.2",["org.antlr.v4.runtime.misc.","org.antlr.v4.runtime.atn.PredictionContext",
                    "org.antlr.v4.runtime.misc.Interval"],[]),
    "metrics-core":("io/dropwizard/metrics/metrics-core/4.2.32",["com.codahale.metrics."],[]),
    "xz":("org/tukaani/xz/1.10",["org.tukaani.xz.lz.","org.tukaani.xz.rangecoder.","org.tukaani.xz.check."],[]),
    "jheaps":("org/jheaps/jheaps/0.14",["org.jheaps.array.","org.jheaps.tree."],[]),
    "json-smart":("net/minidev/json-smart/2.5.2",["net.minidev.json."],[]),
    "asm":("org/ow2/asm/asm/9.7.1",["org.objectweb.asm.Type","org.objectweb.asm.ByteVector","org.objectweb.asm.ConstantDynamic"],[]),
    "log4j-api":("org/apache/logging/log4j/log4j-api/2.26.0",["org.apache.logging.log4j.util.Chars",
                    "org.apache.logging.log4j.util.StringBuilders","org.apache.logging.log4j.Level"],[]),
    "opentelemetry-api":("io/opentelemetry/opentelemetry-api/1.35.0",["io.opentelemetry.api.common.",
                         "io.opentelemetry.api.trace.TraceId","io.opentelemetry.api.trace.SpanId"],[]),
}
SEAL="23111211"
ARMS=("A","B","P","X")

def locate(rev):
    name,v=rev.split("/")[-2:]
    expected=MAVEN/rev/f"{name}-{v}.jar"
    if not expected.is_file():
        choices=sorted(base.archive.MODULES.rglob(expected.name))
        if not choices:
            return None
        expected=choices[0]
    return dict(path=str(expected),sha256=base.sha(expected),coordinate=rev)

def history():
    files=(TOP/"recovery/inputs/manifest.json",TOP/"followup/inputs/power.json",
          TOP/"screening/inputs/research-split.json",TOP/"stable/inputs/schedule.json",
          TOP/"trace-cause/inputs/schedule.json")
    docs=[]
    for f in files:
        if f.exists():
            docs.append((f,f.read_text()))
    return docs

def pick(name,tsv,seen):
    choices={}
    for l in tsv.splitlines():
        cls,method,desc,group,count,branches,mult,loops=l.split("\t")
        # Avoid revisiting any historically measured whole external class.
        outer=cls.partition("$")[0]
        if any(outer in value or outer.replace(".","/") in value for _,value in seen):
            continue
        tag=f"{name}/{cls}.{method}{desc}"
        item=dict(tag=tag,outer=outer,group=group,insns=int(count),branches=int(branches),
                  muldiv=int(mult),backward_jumps=int(loops))
        choices.setdefault(outer,[]).append(item)
    def pick_key(m):
        # One low and one high branch count per project where available, otherwise hashed order.
        return (m["backward_jumps"]>1,sha256(f"{SEAL}\n{m['tag']}".encode()).digest())
    pool=sorted([m for v in choices.values() for m in v],key=pick_key)
    result=[]
    selected=set()
    for quota in (False,True,None):
        for m in pool:
            if m["outer"] in selected or quota is not None and (m["branches"]>=3)!=quota:
                continue
            result.append(m)
            selected.add(m["outer"])
            break
    return result,len(choices)

def static_selection(cpu=14):
    sealed=ROOT/"inputs/static.json"
    if sealed.exists():
        return base.read(sealed)
    out=ROOT/"build/scan"
    out.mkdir(exist_ok=True)
    scan=ROOT/"evidence/scan"
    scan.mkdir(exist_ok=True)
    deps=os.pathsep.join(str(p) for p in base.archive.LOCAL_CP if p.name in ("asm-9.7.1.jar","asm-tree-9.7.1.jar"))
    code=ROOT/"src/ScanPure.java"
    java=["taskset","-c",str(cpu),str(base.archive.JDK/"javac"),"-J-XX:ActiveProcessorCount=1","-cp",deps,"-d",str(out),str(code)]
    subprocess.run(java,check=True,timeout=30,env=base.public_env(scan))
    old=history()
    research=base.read(TOP/"screening/inputs/research-split.json")["splits"]
    withheld={p["project"] for k in ("validation","test") for p in research[k]}
    # Every project in the original locked-test set is excluded as well.
    withheld.update(p["project"] for p in base.read(TOP/"recovery/inputs/manifest.json")["splits"]["test"])
    projects={}
    absent={}
    for name,(rev,scope,extra) in COORDS.items():
        if name in withheld:
            absent[name]="reserved"
            continue
        paths=[locate(s) for s in (rev,*extra)]
        if any(p is None for p in paths):
            absent[name]="local asset missing"
            continue
        cmd=["taskset","-c",str(cpu),*base.archive.jvm(out,"550m"),"-cp",str(out)+os.pathsep+deps,
             "ScanPure",paths[0]["path"],*scope]
        p=subprocess.run(cmd,check=True,timeout=40,text=True,capture_output=True,env=base.public_env(scan))
        f=scan/f"{name}.tsv"
        f.write_text(p.stdout)
        (scan/f"{name}.stderr.log").write_text(p.stderr)
        choices,n=pick(name,p.stdout,old)
        if not choices:
            absent[name]="no fresh family"
            continue
        projects[name]=dict(project=name,files=paths,methods=choices,eligible=n,scan_sha=base.sha(f))
    d=dict(at=base.now(),scope="static outputs only",projects=projects,excluded=absent,
           history={str(f.relative_to(TOP)):base.sha(f) for f,_ in old},scan_sha=base.sha(code))
    base.write(sealed,d)
    print("library panel",len(projects),"distinct projects",absent,flush=True)
    return d

def freeze():
    p=ROOT/"inputs/design.json"
    if p.exists():
        return base.read(p)
    static=static_selection()
    stable=base.read(TOP/"stable/evidence/summary.json")
    admitted=stable["admission"]
    alt=min(admitted,key=lambda a:stable["results"][a]["matched"]["check_ms"]["value"],default=None)
    options=stable["arms"][alt] if alt else {"static-learning":"false"}
    rng=random.Random(231112025)
    jobs=[]
    for i,(name,record) in enumerate(static["projects"].items()):
        order=[(mode,arm) for mode in ("wall","fixed") for arm in ARMS]
        rng.shuffle(order)
        shifts=rng.sample(range(len(order)),4)
        for rep,d in enumerate(shifts):
            tags=[f["tag"] for f in record["methods"]]
            rng.shuffle(tags)
            jobs.append(dict(id=f"{name}-r{rep+1}",project=name,core=base.CPUS[(i+rep)%4],trace=rep>=2,
                            seed=(0,71)[rep%2],tags=tags,order=order[d:]+order[:d]))
    rng.shuffle(jobs)
    data=dict(at=base.now(),protocol=base.sha(TOP/"protocols/12-library-panel.md"),
              static_sha=base.sha(ROOT/"inputs/static.json"),stable_sha=base.sha(TOP/"stable/evidence/summary.json"),
              jobs=jobs,options={a:options if a=="X" else {} for a in ARMS},projects=static["projects"])
    base.write(p,data)
    return data

if __name__=="__main__":
    static_selection(int(os.environ.get("SMT_SCAN_CPU","14")))
