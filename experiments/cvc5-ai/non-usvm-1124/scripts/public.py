"""Bounded public secondary panels from official SMT-LIB logic archives."""
from collections import Counter
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile

from common import ROOT, CPUS, now, read, sha, write
from eval import blocked, summarize

AUBV="ea411cc9f28e6df7bd8ba70d2320bf49"
IDL="6184ac582caed208c956da2bc38e22fe"
SETS={
    "hw":dict(archive="QF_AUFBV.tar.zst",md5=AUBV,logic="QF_AUFBV",
              pattern=r"/2019-Wolf-fmbench/2018E/",maximum=12,files_per_bucket=2),
    "opt":dict(archive="QF_IDL.tar.zst",md5=IDL,logic="QF_IDL",
               pattern=r"/(?:job_shop|schedulingIDL)/",maximum=16,files_per_bucket=4),
}


def get_family(consumer,path):
    file=Path(path).stem
    if consumer=="hw":
        return file.split("-",1)[0]
    if "job_shop/" in path:
        return "job_shop"
    if "_mkspan" in file:
        prefix=file.split("_mkspan",1)[0]
        return "schedulingIDL:"+prefix
    return "schedulingIDL"


def prepare(consumer):
    recipe=SETS[consumer]
    target=ROOT/"inputs/public"/consumer
    meta=target/"manifest.json"
    if meta.exists():
        return read(meta)
    source=ROOT/"inputs/public"/recipe["archive"]
    assert source.is_file(),source
    start=now()
    with source.open("rb") as f:
        assert hashlib.file_digest(f,"md5").hexdigest()==recipe["md5"]
    bucket=Counter()
    candidates=[]
    data_limit=800_000
    with subprocess.Popen(["zstd","-dc",source],stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout,mode="r|") as arc:
            for m in arc:
                if not m.isfile() or not re.search(recipe["pattern"],m.name):
                    continue
                bucket["family_members"]+=1
                if not m.name.endswith(".smt2") or m.size>data_limit:
                    bucket["excluded_size_format"]+=1
                    continue
                text=arc.extractfile(m).read()
                answer=re.search(rb"\(set-info\s+:status\s+(sat|unsat)\s*\)",text)
                license_match=re.search(rb'\(set-info\s+:license\s+"([^"]+)"\s*\)',text)
                category=re.search(rb'\(set-info\s+:category\s+"?([a-z]+)"?\s*\)',text)
                checks=len(re.findall(rb"(?m)^\s*\(check-sat\b",text))
                if not answer or checks!=1:
                    bucket["excluded_metadata_or_nonstatic"]+=1
                    continue
                if not license_match:
                    bucket["missing_file_license"]+=1
                    if consumer!="opt":
                        continue
                family=get_family(consumer,m.name)
                salt=hashlib.sha256(("public-1124-"+m.name).encode()).hexdigest()
                strat="job_shop" if "job_shop/" in m.name else "schedulingIDL" if consumer=="opt" else family
                candidates.append(dict(member=m.name,text=text,expected=answer[1].decode(),family=family,
                                  license=license_match[1].decode() if license_match else None,
                                  archive_license="CC BY 4.0 (Zenodo record 16740866)",
                                  category=category[1].decode() if category else "unknown",
                                  rank=salt,stratum=strat))
        assert process.wait()==0
    admitted=Counter()
    accepted=[]
    # Iterating by score-independent public path hash is reproducible across archives.
    for r in sorted(candidates,key=lambda v:v["rank"]):
        k=(r["stratum"],r["expected"])
        if admitted[k]>=recipe["files_per_bucket"] or len(accepted)>=recipe["maximum"]:
            continue
        admitted[k]+=1
        name="pub-"+r["rank"][:10]
        at=target/(name+".smt2")
        at.parent.mkdir(parents=True,exist_ok=True)
        at.write_bytes(r["text"])
        accepted.append(dict(id=name,producer="public",path=str(at.relative_to(ROOT)),sha256=sha(at),
                             family=r["family"],expected=r["expected"],original=r["member"],license=r["license"],
                             archive_license=r["archive_license"],category=r["category"],
                             bytes=at.stat().st_size))
    data=dict(start=start,stop=now(),consumption=consumer,logic=recipe["logic"],original_md5=recipe["md5"],sha256=sha(source),
              counts=dict(bucket,eligible=len(candidates),admitted=len(accepted)),items=accepted)
    write(meta,data)
    print(now(),consumer,data["counts"],flush=True)
    return data


def run(consumer,cap):
    plan_name=consumer+"-search"
    locked=ROOT/"evidence"/f"{plan_name}-lock.json"
    assert locked.exists(),"public outcomes are collected after main settings lock"
    setup=read(locked)
    data=prepare(consumer)
    path=ROOT/"raw"/f"public-{consumer}"
    if not data["items"]:
        write(path/"summary.json",dict(at=now(),oracle={},excluded=[],n=0,preparation=data["counts"],
                                       summary={},lock_sha256=sha(locked),status="no-compatible-input"))
        return
    oracle=blocked(path/"z3",data["items"],{"Z3":[]},30.,1,779003,"z3")
    excluded={r["id"] for r in oracle if r["status"] in ("wrong","error")}
    items=[r for r in data["items"] if r["id"] not in excluded]
    alias={}
    options={"default":[],"fixed":setup["contrast"],**{k:v["flags"] for k,v in setup["selected"].items()}}
    actual={}
    first={}
    for name,flags in options.items():
        if tuple(flags) not in first:
            first[tuple(flags)]=name
            actual[name]=flags
        alias[name]=first[tuple(flags)]
    rows=blocked(path/"cvc5",items,actual,cap,2,779000)
    write(path/"summary.json",dict(at=now(),oracle=summarize(oracle),excluded=sorted(excluded),n=len(items),
                                   alias=alias,actual=actual,summary=summarize(rows),preparation=data["counts"],lock_sha256=sha(locked)))
    print(now(),consumer,summarize(rows),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("prepare","run"))
    p.add_argument("consumer",choices=SETS)
    p.add_argument("--cap",type=float,default=7.)
    a=p.parse_args()
    if a.action=="prepare":
        prepare(a.consumer)
    else:
        run(a.consumer,a.cap)
