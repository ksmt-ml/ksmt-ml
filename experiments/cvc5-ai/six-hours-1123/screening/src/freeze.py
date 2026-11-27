"""Static setup only. No held-out method is executed to decide its inclusion."""
from pathlib import Path
import collections
from hashlib import sha256
import os
import random
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base

COORDINATES={
    "guava":("train","com/google/guava/guava/33.4.8-jre",["com.google.common.math.","com.google.common.primitives."],[]),
    "commons-math3":("train","org/apache/commons/commons-math3/3.6.1",["org.apache.commons.math3.util.","org.apache.commons.math3.special."],[]),
    "fastutil":("train","it/unimi/dsi/fastutil/8.5.12",["it.unimi.dsi.fastutil.ints.","it.unimi.dsi.fastutil.longs.","it.unimi.dsi.fastutil.bytes."],[]),
    "commons-lang3":("validation","org/apache/commons/commons-lang3/3.18.0",["org.apache.commons.lang3."],[]),
    "HdrHistogram":("validation","org/hdrhistogram/HdrHistogram/2.2.2",["org.HdrHistogram."],[]),
    "joda-time":("validation","joda-time/joda-time/2.13.0",["org.joda.time."],[]),
    "commons-collections4":("test","org/apache/commons/commons-collections4/4.5.0",["org.apache.commons.collections4."],[]),
    "jgrapht":("test","org/jgrapht/jgrapht-core/1.5.2",["org.jgrapht.alg.","org.jgrapht.util."],["org/jheaps/jheaps/0.14"]),
    "caffeine":("test","com/github/ben-manes/caffeine/caffeine/3.2.0",["com.github.benmanes.caffeine.cache."],[]),
    "icu4j":("test","com/ibm/icu/icu4j/75.1",["com.ibm.icu.lang.","com.ibm.icu.impl.","com.ibm.icu.math."],[]),
    "commons-io":("test","commons-io/commons-io/2.17.0",["org.apache.commons.io.EndianUtils",
                   "org.apache.commons.io.ByteOrderMark","org.apache.commons.io.IOUtils"],[]),
    "asm":("test","org/ow2/asm/asm/9.7.1",["org.objectweb.asm."],[]),
}
MAVEN=Path.home()/".m2/repository"
SALT="2311-warm-fixed-v1"
SCANNER_CPU=str(int(os.environ.get("SMT_SCAN_CPU","14")))
ASSIGNMENT_KEYS={"scalar","array","floating"}

def locate(where):
    name,version=where.split("/")[-2:]
    p=MAVEN/where/f"{name}-{version}.jar"
    if not p.is_file():
        found=sorted(base.archive.MODULES.rglob(p.name))
        if not found:
            return None
        p=found[0]
    return dict(path=str(p),coordinate=where,sha256=base.sha(p))

def read_history():
    earlier=list((ROOT.parent.parent/"overnight/protocols").glob("*.json"))
    earlier += [base.OLD/"inputs/manifest.json",base.ROOT/"inputs/manifest.json",
                ROOT.parent/"followup/inputs/power.json"]
    names=sorted(p for p in earlier if p.is_file())
    body=[p.read_text(errors="replace") for p in names]
    return body,{str(p):base.sha(p) for p in names}

def select(project, path, history):
    buckets=collections.defaultdict(list)
    for row in path.read_text().splitlines():
        cls,name,sig,group,count,branches,mul,loops=row.split("\t")
        baseclass=cls.split("$",1)[0]
        if any(baseclass in p or baseclass.replace(".","/") in p for p in history):
            in_archive=True
        else:
            in_archive=False
        tag=f"{project}/{cls}.{name}{sig}"
        if any(tag in p for p in history):
            continue
        item=dict(tag=tag,outer_class=baseclass,theory_hint=group,bytecode=int(count),
                  branches=int(branches),mul_div_rem=int(mul),backward_jumps=int(loops),historical_class=in_archive)
        buckets[group].append(item)
    assert buckets.keys() <= ASSIGNMENT_KEYS
    def rank(m):
        return m["historical_class"], sha256(f"{SALT}|{m['tag']}".encode()).digest()
    methods=[]
    classes=set()
    for g in ("scalar","array","floating","scalar","array","floating"):
        for m in sorted(buckets[g],key=rank):
            if m["outer_class"] in classes:
                continue
            methods.append(m)
            classes.add(m["outer_class"])
            break
    variants=sorted((m for lst in buckets.values() for m in lst),key=rank)
    for m in variants:
        if len(methods)>=5:
            break
        if m["outer_class"] not in classes:
            methods.append(m)
            classes.add(m["outer_class"])
    if not methods:
        return [],dict()
    return methods,{g:len({m["outer_class"] for m in v}) for g,v in buckets.items()}

def main():
    assignment=ROOT/"inputs/split.json"
    if assignment.is_file():
        return
    out=ROOT/"build/scan"
    out.mkdir(exist_ok=True)
    scan=ROOT/"evidence/scan"
    scan.mkdir(exist_ok=True)
    deps=os.pathsep.join(str(p) for p in base.archive.LOCAL_CP if p.name in ("asm-9.7.1.jar","asm-tree-9.7.1.jar"))
    javac=[str(base.archive.JDK/"javac"),"-J-XX:ActiveProcessorCount=1","-cp",deps,"-d",str(out),
           str(ROOT/"src/ScanPure.java")]
    subprocess.run(["taskset","-c",SCANNER_CPU,*javac],check=True,timeout=36,env=base.public_env(out))
    history,hashes=read_history()
    groups={"train":[],"validation":[],"test":[]}
    excluded={}
    for project,(split,coord,prefix,extra) in COORDINATES.items():
        files=[locate(p) for p in (coord,*extra)]
        if any(p is None for p in files):
            excluded[project]="installed jar missing"
            continue
        report=scan/f"{project}.tsv"
        cmd=["taskset","-c",SCANNER_CPU,*base.archive.jvm(out,"500m"),"-cp",str(out)+os.pathsep+deps,
             "ScanPure",files[0]["path"],*prefix]
        result=subprocess.run(cmd,text=True,timeout=45,check=True,env=base.public_env(out),capture_output=True)
        report.write_text(result.stdout)
        (scan/f"{project}.stderr.log").write_text(result.stderr)
        selected,eligible=select(project,report,history)
        if len(selected)<2:
            excluded[project]={"reason":"fewer than two eligible outer classes", "eligible":eligible}
            continue
        groups[split].append(dict(project=project,files=files,methods=selected,eligible=eligible,scan_sha=base.sha(report)))
    base.write(assignment,dict(at=base.now(),salt=SALT,splits=groups,excluded=excluded,history=hashes,
                              scanner_source=base.sha(ROOT/"src/ScanPure.java")))
    print("frozen", {k:{p["project"]:len(p["methods"]) for p in v} for k,v in groups.items()},excluded,flush=True)

if __name__=="__main__":
    main()
