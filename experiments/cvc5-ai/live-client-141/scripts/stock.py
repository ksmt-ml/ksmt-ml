"""Bounded supplementary check of the original USVM branch error handling."""
import concurrent.futures
import itertools
import json
import re
import resource
import subprocess
import sys
import time
from collections import Counter, defaultdict

from common import *
import run

PRIMARY = ROOT / "runs/primary"
OUT = ROOT / "runs/stock"
DESIGN = ROOT / "DIAGNOSTIC-PROTOCOL.md"

def should_run():
    m = read(PRIMARY / "metrics.json")
    d = read(PRIMARY / "diagnostics.json")["comparisons"]["cvc5-none/cvc5-default"]["material"]
    strict_errors = Counter()
    for p in PRIMARY.glob("*/*/results.jsonl"):
        for line in p.read_text().splitlines():
            r = json.loads(line)
            if r["status"] != "ok":
                strict_errors[p.parent.parent.name, r["tag"]] += 1
    all_arm_errors = sum(value == 3 for value in strict_errors.values())
    material = any(d.values())
    return dict(run=all_arm_errors >= 48 or material, all_three_error_method_blocks=all_arm_errors,
                intended_method_blocks=480, material_coverage_primary_cvc5=material)

def parity(items):
    order = {a:i for i,a in enumerate(ARMS)}
    return sum(order[a] > order[b] for a,b in itertools.combinations(items,2)) % 2

def schedule():
    frozen = read(ROOT / "inputs/schedule.json")
    choices = {}
    jobs = []
    for job in frozen["jobs"]:
        even = parity(job["arms"])
        selected = choices.setdefault(job["project"],even)
        if even == selected:
            jobs.append(job)
    assert len(jobs) == 30
    for name in choices:
        subset = [j for j in jobs if j["project"] == name]
        assert len(subset) == 3
        assert all({j["arms"][i] for j in subset} == set(ARMS) for i in range(3))
    return dict(locked=now(), seed=frozen["seed"], primary_schedule_sha256=sha(ROOT / "inputs/schedule.json"),
                jobs=jobs, parity_per_project=choices)

def compile_overlay():
    src = USVM / "usvm-jvm/src/test/kotlin/org/usvm/dataset/Live141Driver.kt"
    dest = BUILD / "stock"
    dest.mkdir(exist_ok=False)
    source = dest / "Live141Stock.kt"
    s = src.read_text()
    marker = "throwExceptionOnStepFailure = true"
    assert s.count(marker) == 1
    s = s.replace(marker, "throwExceptionOnStepFailure = false")
    s = s.replace('            event("method_start", tag)\n', '            event("method_start", tag)\n'
                  '            println("STOCK_METHOD_START $tag")\n')
    s = s.replace('            event("method_end", tag)\n', '            event("method_end", tag)\n'
                  '            println("STOCK_METHOD_END $tag")\n')
    source.write_text(s)
    compiler_names = (
        "kotlin-compiler-embeddable-2.1.0.jar", "kotlin-stdlib-2.1.0.jar",
        "kotlin-script-runtime-2.1.0.jar", "kotlin-reflect-1.6.10.jar",
        "kotlin-daemon-embeddable-2.1.0.jar", "trove4j-1.0.20200330.jar",
        "kotlinx-coroutines-core-jvm-1.6.4.jar", "annotations-13.0.jar",
    )
    compiler = os.pathsep.join(str(next(MODULES.rglob(name))) for name in compiler_names)
    scratch = dest / "scratch"
    scratch.mkdir()
    friends = [BUILD /"client", *[p for p in LOCAL_CP if str(p).startswith(str(ORIGINAL)) and p.exists()]]
    cmd = ["taskset","-c","14",*jvm(scratch,"1g"),"-cp",compiler,"org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
           "-no-stdlib","-no-reflect","-jvm-target","1.8","-classpath",run.RUNTIME_CP,
           "-Xfriend-paths=" + ",".join(map(str,friends)),"-d",str(dest),str(source)]
    with (OUT/"compile.log").open("w") as log:
        print(now(),json.dumps(cmd),file=log,flush=True)
        result = subprocess.run(cmd,cwd=ROOT,env=safe_env(scratch),stdout=log,stderr=subprocess.STDOUT,
                                timeout=75)
        print(now(),"returncode",result.returncode,file=log,flush=True)
        result.check_returncode()
    shutil.rmtree(scratch)
    run.RUNTIME_CP = str(dest) + os.pathsep + run.RUNTIME_CP
    run.MAIN = "org.usvm.dataset.Live141StockKt"
    return {str(p.relative_to(ROOT)):sha(p) for p in [source,*dest.rglob("*.class")]}

def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    OUT.mkdir()
    decision = should_run()
    write(OUT/"decision.json",dict(at=now(),protocol_sha256=sha(DESIGN),**decision))
    if not decision["run"]:
        print("Supplementary acquisition is below the diagnostic threshold.",flush=True)
        return
    subprocess.run([sys.executable, str(ROOT/"scripts/status.py"),
                    "Running 30 supplementary live blocks with USVM stock step-error handling",
                    "Primary acquisition and independent verification complete|Primary result retained with all failures|Frozen diagnostic trigger set",
                    "Finish supplementary paired blocks|Audit lost states and time sensitivity|Write and verify Russian report"],
                   check=True,cwd=ROOT)
    compiled = compile_overlay()
    derived = schedule()
    write(OUT/"derived-schedule.json",derived)
    assets = read(PRIMARY/"lock.json")["release"]
    write(OUT/"lock.json",dict(start=now(),main_input=sha(PRIMARY/"metrics.json"),derived_schedule=sha(OUT/"derived-schedule.json"),
                             stock_overlay=compiled, operator="throwExceptionOnStepFailure=false", release=assets))
    project = {p["project"]:p for p in read(ROOT/"inputs/manifest.json")["projects"]}
    cps = {n:run.prior_inputs(p) for n,p in project.items()}
    j = OUT/"supervisor.jsonl"
    run.append(j,"campaign_start")
    def lane(core):
        for block in derived["jobs"]:
            if block["core"] != core:
                continue
            if datetime.now(TZ).timestamp()+run.CAP*3 > run.HARD_STOP.timestamp() or run.disk_used()>11_500_000_000:
                run.append(j,"block_unscheduled_deadline_or_gate",block=block["id"],core=core)
                continue
            run.append(j,"block_start",block=block["id"],core=core,workspace_bytes=run.disk_used())
            for pos,arm in enumerate(block["arms"],1):
                where = OUT/block["id"]/arm
                run.append(j,"case_start",block=block["id"],arm=arm,position=pos,core=core)
                status = run.once(where,arm,core,[f"cp={cps[block['project']]}"],block["tags"])
                verified = False
                if (where/"backend.json").exists():
                    try:
                        run.verify_backend(where,arm,core,assets)
                        verified = True
                    except Exception as exc:
                        run.append(j,"identity_mismatch",block=block["id"],arm=arm,detail=repr(exc))
                run.append(j,"case_end",block=block["id"],arm=arm,position=pos,core=core,
                           identity_verified=verified,returncode=status["returncode"],emitted=len(status["emitted"]),
                           timeout=status["outer_timeout"],wall_ms=status["wall_ms"])
            run.append(j,"block_end",block=block["id"],core=core)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks=[pool.submit(lane,c) for c in CORES]
        for task in concurrent.futures.as_completed(tasks):
            try:
                task.result()
            except Exception as exc:
                run.append(j,"lane_error",detail=repr(exc))
    run.append(j,"campaign_end",workspace_bytes=run.disk_used())

if __name__=="__main__":
    main()
