"""Fresh USVM sessions grouped by operation skeleton, sealed before controller choices."""
import argparse
from collections import Counter
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
import subprocess
import sys
import time

from replay import CAMPAIGN, CORES, DATA, PYTHON, ROOT, STOP, check_budget, digest, ev, guarded_popen, load, log, now, lines
from report import write_json

USVM = CAMPAIGN / "v1/usvm"
sys.path.insert(0,str(USVM))
from java_programs import make_group

PRIORS = [
    CAMPAIGN/"setup/usvm-pilot",
    DATA/"train", DATA/"validation",
    CAMPAIGN/"runs/campaign-v1/final/test",
    ROOT.parent/"interactive/runs/interactive-v1/final/test",
]
FAMILIES = ("mix","product","signed","shift","mask","sum","remainder")
WIDTHS = (5,7,9,11,12)
PRIVATE = ROOT/"private"
JDK = Path("/usr/lib/jvm/java-21-openjdk/bin")

def commitment(split, raw):
    return hashlib.sha256(b"cvc5-overnight-2026-11-22\0" + split.encode() + b"\0" + raw).hexdigest()

def seal(split, families, variants, min_admitted, method_ms=5000, solver_ms=1800):
    PRIVATE.mkdir(mode=0o700,exist_ok=True)
    seedpath=PRIVATE/f"{split}.seed"
    plan=ROOT/"protocols"/f"{split}-seed.json"
    if plan.exists():
        previous=load(plan)
        assert (previous["families"],previous["variants"],previous["minimum_programs"],
                previous["method_ms"],previous["solver_ms"])==(families,variants,min_admitted,method_ms,solver_ms)
        return previous
    raw=secrets.token_bytes(16)
    with os.fdopen(os.open(seedpath,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"w") as f:
        f.write(raw.hex()+"\n")
    plan_data={"created":now(),"split":split,"seed_commitment_sha256":commitment(split,raw),
               "families":families,"variants":variants,"minimum_programs":min_admitted,
               "method_ms":method_ms,"solver_ms":solver_ms,"widths":WIDTHS,
               "split_unit":"ordered sequence of generation operation kinds; family disjoint across splits",
               "filters":"whole trace, expected oracle answers, valid completion, independent Z3 audit, normalization and no exact overlap"}
    write_json(plan,plan_data)
    plan.chmod(0o444)
    return plan_data

def skeleton(item):
    return tuple(t.split(":")[1] for t in item["shape_tokens"])

def normalized_source(data):
    return hashlib.sha256(re.sub(rb"\bpublic class Program\d+\b",b"public class Program",data,count=1)).hexdigest()

class FamilyRng(random.Random):
    """Reuse the frozen generator, fixing its operation kinds for one family."""
    def __init__(self, seed, family):
        super().__init__(seed)
        self.ops = iter(family)
        self.length = len(family)

    def randrange(self, *args):
        if args == (3,7):
            return self.length
        return super().randrange(*args)

    def choice(self, seq):
        if seq == FAMILIES:
            return next(self.ops)
        return super().choice(seq)

def generated(target, seed, plan, priors):
    target.mkdir(parents=True,exist_ok=False)
    source=target/"source/pilot"
    source.mkdir(parents=True)
    rng=random.Random(seed)
    programs=[]
    prior_programs=[(folder,p) for folder in priors if (folder/"programs.json").exists()
                    for p in load(folder/"programs.json")["programs"] if p.get("shape_tokens")]
    shapes=[set(p["shape_tokens"]) for _,p in prior_programs]
    known_families={skeleton(p) for _,p in prior_programs}
    known_source={normalized_source((d/p["source"]).read_bytes()) for d,p in prior_programs}
    for family_index in range(plan["families"]):
        for _ in range(3000):
            family=tuple(rng.choice(FAMILIES) for _ in range(rng.randrange(3,7)))
            if family not in known_families:
                known_families.add(family)
                break
        else:
            raise RuntimeError("not enough distinct operation families")
        family_id="-".join(family)
        for variant in range(plan["variants"]):
            width=plan["widths"][(family_index + 2*variant) % len(plan["widths"])]
            index=len(programs)
            for _ in range(3000):
                cls,java,shape=make_group(FamilyRng(rng.getrandbits(64),family),index,width)
                tokens=set(shape)
                normalized=normalized_source(java.encode())
                similarity=max((len(tokens&base)/len(tokens|base) for base in shapes),default=0)
                if similarity < .72 and normalized not in known_source:
                    break
            else:
                raise RuntimeError(f"cannot generate non-duplicate within family {family_id}")
            known_source.add(normalized)
            shapes.append(tokens)
            path=source/f"{cls}.java"
            path.write_text(java)
            programs.append({"class":f"pilot.{cls}","group":f'{plan["split"]}-{cls}',"family":family_id,
                             "width":width,"shape_tokens":shape,"source":str(path.relative_to(target)),
                             "max_prior_similarity":similarity,"sha256":digest(path),"normalized_sha256":normalized})
    write_json(target/"programs.json",
               {"seed":f"{seed:032x}","split":plan["split"],"programs":programs,
                "exclusions":{os.path.relpath(d/"programs.json",ROOT):digest(d/"programs.json") for d in priors if (d/"programs.json").exists()}})

def commands_log(path,cmd,cap,cwd=ROOT):
    check_budget()
    cap=min(cap,max(.01,STOP-time.time()-12))
    with path.open("ab") as out:
        out.write((json.dumps({"start":now(),"args":[str(v) for v in cmd],"timeout_s":cap})+"\n").encode())
        out.flush()
        proc=guarded_popen(cmd,cwd=cwd,stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=True)
        import signal
        until=time.monotonic()+cap
        try:
            while proc.poll() is None:
                check_budget()
                if time.monotonic()>=until:
                    raise subprocess.TimeoutExpired(cmd,cap)
                time.sleep(.5)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait()
            out.write((json.dumps({"end":now(),"returncode":proc.returncode})+"\n").encode())
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode,cmd)

def trace(target, core, plan, *, jars=(), package="pilot.", classes=None, project="pilot", shard_seed=1871):
    check_budget()
    config=dict(line.split("=",1) for line in (CAMPAIGN/"setup/usvm-classpath.env").read_text().splitlines()
                if line.startswith(("CP=","APIJAR=","APPROXJAR=")))
    for name in ("traces","java-tmp","java-home"):
        (target/name).mkdir(exist_ok=True)
    if classes is None and not jars:
        classes=target/"classes"
        classes.mkdir()
        commands_log(target/"materialization.log",
                     ["taskset","-c",str(core),JDK/"javac","-J-XX:-UsePerfData",
                      f"-J-Djava.io.tmpdir={target/'java-tmp'}","--release","17","-d",classes,
                      *sorted((target/"source/pilot").glob("*.java"))],60,cwd=target)
    targets=tuple(jars) if classes is None else (classes,)
    classpath=os.pathsep.join(map(str,targets))
    expected=plan.get("families",0)*plan.get("variants",0)
    cap=min(1500,60+max(expected,plan.get("max_classes",0),12)*(3+plan["method_ms"]/1000))
    cmd=[
        "taskset","-c",str(core),"env",
        "MALLOC_ARENA_MAX=2",f"TMPDIR={target/'java-tmp'}",f"USVM_SESSION_DIR={target/'traces'}",
        f"USVM_SMT_DIR={target/'traces'}","USVM_SMT_MAX_PER_TAG=0",
        f'usvm.jvm.api.jar.path={config["APIJAR"]}',f'usvm.jvm.approximations.jar.path={config["APPROXJAR"]}',
        JDK/"java","-Xmx2g","-XX:ActiveProcessorCount=1","-XX:+UseSerialGC","-XX:-UsePerfData",
        f"-Duser.home={target/'java-home'}",f"-Djava.io.tmpdir={target/'java-tmp'}",
        "-cp",config["CP"],"org.usvm.dataset.DatasetDriverKt",
        f"project={project}",f"cp={classpath}",f"targets={classpath}",f"pkg={package}",
        f'methodTimeoutMs={plan["method_ms"]}',f'solverTimeoutMs={plan["solver_ms"]}',
        f"seed={shard_seed}",f"totalSeconds={int(cap)-15}","maxMethodsPerClass=1",
        f'maxClasses={plan.get("max_classes",100000)}',"rssLimitMb=3500"]
    commands_log(target/"usvm-driver.log",cmd,cap,cwd=target)

def audited_manifest(target, core, priors, minimum):
    manifest=target/"manifest.json"
    original=load(manifest)
    write_json(target/"pre-audit-manifest.json",original)
    known_hashes={r["sha256"] for d in priors if (d/"manifest.json").exists() for r in load(d/"manifest.json")["sessions"]}
    known_sources={r["source_sha256"] for d in priors if (d/"manifest.json").exists() for r in load(d/"manifest.json")["sessions"]}
    group_n=Counter(r["group"] for r in original["sessions"])
    session_hash_n=Counter(r["sha256"] for r in original["sessions"])
    source_hash_n=Counter(r["source_sha256"] for r in original["sessions"])
    checked=[]
    for row in original["sessions"]:
        reason=None
        if row["operations"].get("open")!=1 or row["operations"].get("close",0)>1:
            reason="invalid trace open or repeated close"
        elif row["sha256"] in known_hashes or row["source_sha256"] in known_sources:
            reason="exact overlap with previous split"
        elif group_n[row["group"]] != 1 or session_hash_n[row["sha256"]]!=1 or source_hash_n[row["source_sha256"]]!=1:
            reason="within-split duplicate or multiple sessions for same unit"
        if reason:
            original["rejected"].append({"file":row["file"],"reason":reason,"sha256":row["sha256"]})
        else:
            checked.append(row)
    original["sessions"]=checked
    original["group_count"]=len(checked)
    interim=target/"audit-input.json"
    write_json(interim,original)
    audit_dir=target/"z3-audit.jsonl"
    try:
        commands_log(target/"z3-audit.log",
                     [PYTHON,"-B",USVM/"audit.py",interim,audit_dir,"--core",str(core)],
                     max(30,18*len(checked)+5),cwd=target)
    except subprocess.CalledProcessError:
        # The audit returns nonzero on mismatches. Retain and exclude those traces.
        if not audit_dir.exists() or len(lines(audit_dir))!=len(checked):
            raise
    audits={r["session"]:r for r in lines(audit_dir)} if audit_dir.exists() else {}
    final=[]
    for r in checked:
        a=audits[r["file"]]
        if a["status"]=="ok" and a["answers"]==r["oracle"]:
            final.append(r)
        else:
            original["rejected"].append({"file":r["file"],"reason":f'Z3 audit {a["status"]}',"sha256":r["sha256"]})
    original["sessions"]=final
    original["group_count"]=len(final)
    write_json(manifest,original)
    ev.verify(manifest)
    if len(final)<minimum:
        raise RuntimeError(f"only {len(final)} verified sessions of {minimum} required")
    return original

def materialize(split, lock, target, extra_priors=(), core=10):
    plan=load(ROOT/"protocols"/f"{split}-seed.json")
    assert isinstance(lock,Path) and lock.is_file()
    lock_data=load(lock)
    assert lock_data["locked"] >= plan["created"], "all decisions must be frozen after seed commitment"
    raw=bytes.fromhex((PRIVATE/f"{split}.seed").read_text().strip())
    assert commitment(split,raw)==plan["seed_commitment_sha256"]
    priors=[*PRIORS,*extra_priors]
    log(f"START materialize {split}, release after {lock.relative_to(ROOT)}")
    generated(target,int.from_bytes(raw,"big"),plan,priors)
    write_json(target/"release.json",{"unlocked":now(),"decision_lock":os.path.relpath(lock,ROOT),
               "decision_lock_sha256":digest(lock),"commitment_sha256":commitment(split,raw),
               "seed":raw.hex()})
    trace(target,core,plan)
    commands_log(target/"materialization.log",
                 ["taskset","-c",str(core),PYTHON,"-B",USVM/"convert.py",target],120,cwd=target)
    manifest=target/"manifest.json"
    m=load(manifest)
    by_src={r["sha256"]:r for r in load(target/"programs.json")["programs"]}
    for row in m["sessions"]:
        row["family"]=by_src[row["source_sha256"]]["family"]
    write_json(manifest,m)
    m=audited_manifest(target,core,priors,plan["minimum_programs"])
    for path in (manifest,target/"programs.json",target/"release.json"):
        path.chmod(0o444)
    log(f"VERIFY {split}: {m['group_count']} программ, {len(set(r['family'] for r in m['sessions']))} семейств, {len(m['rejected'])} отклонённых")
    return manifest

if __name__=="__main__":
    for split,families,variants,minimum in [
        ("portfolio-dev",16,2,22),("portfolio-test",22,2,30),
        ("search-test",24,2,33),("calibration-test",20,2,28),
    ]:
        seal(split,families,variants,minimum)
