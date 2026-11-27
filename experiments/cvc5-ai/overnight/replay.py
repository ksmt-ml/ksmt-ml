"""Pinned and randomized whole-session measurements with independent verification."""
import argparse
from collections import Counter
from datetime import datetime
import ctypes
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

from report import ROOT, write_json

CAMPAIGN = ROOT.parent / "campaign"
DATA = CAMPAIGN / "dataset-v1"
PYTHON = CAMPAIGN / ".venv/bin/python"
CORES = (8, 9, 10, 11, 12, 14)
STOP = datetime.fromisoformat("2026-11-22T10:48:00+03:00").timestamp()
sys.path.append(str(CAMPAIGN / "v1"))
import evaluator as ev

LIBC=ctypes.CDLL(None,use_errno=True)
PR_SET_PDEATHSIG=1

def guarded_popen(*args,preexec_fn=None,**kwargs):
    """Keep an isolated solver bounded even if its measuring worker exits."""
    parent=os.getpid()
    def before_exec():
        if LIBC.prctl(PR_SET_PDEATHSIG,signal.SIGKILL,0,0,0)!=0:
            os._exit(126)
        if os.getppid()!=parent:
            os._exit(125)
        if preexec_fn is not None:
            preexec_fn()
    return subprocess.Popen(*args,preexec_fn=before_exec,**kwargs)

# The frozen evaluator still constructs the same argv and computes its metrics.
# Only the Linux lifecycle of its direct child is strengthened in new workers.
ev.subprocess=SimpleNamespace(Popen=guarded_popen,PIPE=subprocess.PIPE,
                              TimeoutExpired=subprocess.TimeoutExpired)
ORIGINAL_FLAGS=ev.flags


def load(path):
    return json.loads(Path(path).read_text())

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def lines(path):
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream]

def append(path, row):
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def log(message):
    with (ROOT / "research.log").open("a") as out:
        out.write(f"{now()} {message}\n")

def check_budget():
    if time.time() >= STOP:
        raise TimeoutError("hard measurement deadline")
    used = disk_bytes()
    if used >= 13_500_000_000:
        raise RuntimeError(f"disk guard: {used} bytes")
    return used

def disk_bytes():
    """Allow an ephemeral file to vanish during an apparent-size snapshot."""
    samples = []
    for _ in range(3):
        attempt = subprocess.run(["du", "-sb", ROOT], text=True, capture_output=True,
                                 env=dict(os.environ, LC_ALL="C"), timeout=5)
        if attempt.stdout.strip():
            samples.append(int(attempt.stdout.split()[0]))
        if attempt.returncode == 0:
            return max(samples)
        diagnostics = attempt.stderr.splitlines()
        transient = diagnostics and all(line.endswith(": No such file or directory") for line in diagnostics)
        if not transient or not samples:
            raise subprocess.CalledProcessError(
                attempt.returncode, attempt.args, output=attempt.stdout, stderr=attempt.stderr)
        time.sleep(.015)
    return max(samples)

def verify_frozen():
    frozen = load(DATA / "frozen.json")
    actual = {}
    for name, expected in frozen["files_sha256"].items():
        path = CAMPAIGN / name
        got = digest(path)
        if got != expected:
            raise ValueError(f"frozen file mismatch: {name}")
        actual[name] = got
    return actual

def pair_blocks(plan, items, worker):
    for repeat in range(worker, plan["repeats"], len(plan["cores"])):
        rng = random.Random(plan["seed"] + repeat * 2_654_435_761)
        groups = list(items)
        rng.shuffle(groups)
        for item in groups:
            arms = list(plan["configs"])
            rng.shuffle(arms)
            yield repeat, item, arms

def restart_measure(manifest, item, first, policy, limit, remaining):
    start = time.monotonic()
    deadline = start + min(limit, remaining)
    budget_a = float(policy["first_s"])
    trial_a = ev.measure(manifest, item, first, budget_a, deadline)
    trial = trial_a
    attempts = [trial_a]
    budget_b = deadline - time.monotonic()
    if trial_a["status"] == "timeout" and budget_b > .005:
        trial = ev.measure(manifest, item, policy["second"], budget_b)
        attempts.append(trial)
    elapsed = time.monotonic() - start
    status = trial["status"]
    if status == "solved" and elapsed > limit:
        status = "timeout"
    if status == "timeout" and remaining < limit:
        status = "budget"
    return {**trial, "status":status, "wall_s":elapsed,
            "solver_cpu_s":sum(r["solver_cpu_s"] for r in attempts),
            "par2":None if status=="budget" else elapsed if status=="solved" else 2*limit,
            "attempts": attempts, "secondary_budget_s":budget_b if len(attempts)==2 else None}


def worker(path, slot):
    plan = load(path / "plan.json")
    cpu = plan["cores"][slot]
    os.sched_setaffinity(0, {cpu})
    manifest = ROOT / plan["manifest"]
    if digest(manifest) != plan["manifest_sha256"]:
        raise ValueError("manifest changed after plan")
    items = ev.verify(manifest)
    alternatives={}
    for arm,alternate in plan.get("manifest_by_arm",{}).items():
        m=ROOT/alternate["path"]
        assert digest(m)==alternate["sha256"]
        alternatives[arm]=(m,{x["group"]:x for x in ev.verify(m)})
    out = path / f"worker-{slot}.jsonl"
    if out.exists():
        raise FileExistsError(out)
    max_time = min(plan["deadline"], STOP)
    for repeat, item, arms in pair_blocks(plan, items, slot):
        for position, arm in enumerate(arms):
            remaining = max_time - time.time()
            if remaining < 0.01:
                raise TimeoutError("replay stopped at deadline")
            arm_limit = plan.get("timeout_by_arm", {}).get(arm, plan["timeout_s"])
            ev.SOLVER_SEED = plan.get("solver_seed_by_arm", {}).get(arm, 18473)
            measured_manifest,measured_item=manifest,item
            if arm in alternatives:
                measured_manifest,catalog=alternatives[arm]
                measured_item=catalog[item["group"]]
                assert item["oracle"]==measured_item["oracle"]
            extra=plan.get("extra_flags_by_arm",{}).get(arm,[])
            ev.flags=lambda params: [*ORIGINAL_FLAGS(params),*extra]
            policy = plan.get("fallback_by_arm", {}).get(arm)
            try:
                if policy is None:
                    measurement = ev.measure(measured_manifest, measured_item, plan["configs"][arm], arm_limit,
                                             time.monotonic() + remaining)
                else:
                    measurement = restart_measure(measured_manifest, measured_item, plan["configs"][arm], policy, arm_limit, remaining)
            finally:
                ev.flags=ORIGINAL_FLAGS
            if arm in alternatives:
                measurement["measured_session"]=measurement["session"]
                measurement["session"]=item["file"]
            append(out, {"repeat":repeat, "position":position, "arm":arm,
                         "core":cpu, **measurement})
    write_json(path / f"worker-{slot}.done.json", {"completed":now(), "core":cpu})

def verify_replay(path, publish=True):
    plan = load(path / "plan.json")
    manifest = ROOT / plan["manifest"]
    assert digest(manifest) == plan["manifest_sha256"]
    inputs = load(manifest)["sessions"]
    by_session = {item["file"]:item for item in inputs}
    alternatives={}
    for arm,alternate in plan.get("manifest_by_arm",{}).items():
        m=ROOT/alternate["path"]
        assert digest(m)==alternate["sha256"]
        mapping={x["group"]:x for x in load(m)["sessions"]}
        assert set(mapping)=={x["group"] for x in inputs}
        for row in inputs:
            other=mapping[row["group"]]
            assert other["oracle"]==row["oracle"]
            assert digest(m.parent/other["file"])==other["sha256"]
        alternatives[arm]=mapping
    assert len(by_session) == len({item["group"] for item in inputs}) == len(inputs)
    assert all(digest(manifest.parent / file) == item["sha256"] for file, item in by_session.items())
    counts = {arm:Counter() for arm in plan["configs"]}
    sums = {arm:[] for arm in plan["configs"]}
    row_count = 0
    for slot, cpu in enumerate(plan["cores"]):
        assert (path / f"worker-{slot}.done.json").is_file()
        stream = lines(path / f"worker-{slot}.jsonl")
        # Rebuild the permutation independently from the published PRNG rule.
        expected = []
        for repeat in range(plan["repeats"]):
            if repeat % len(plan["cores"]) != slot:
                continue
            rng = random.Random(plan["seed"] + repeat * 2_654_435_761)
            order = list(by_session)
            rng.shuffle(order)
            for file in order:
                arms = list(plan["configs"])
                rng.shuffle(arms)
                expected.extend((repeat, file, arm, position) for position, arm in enumerate(arms))
        actual = [(r["repeat"],r["session"],r["arm"],r["position"]) for r in stream]
        assert actual == expected
        for row in stream:
            truth = by_session[row["session"]]
            assert row["core"] == cpu and row["group"] == truth["group"]
            if row["arm"] in alternatives:
                assert row["measured_session"]==alternatives[row["arm"]][row["group"]]["file"]
            assert row["queries"] == len(truth["oracle"])
            observed = row["answers"]
            matches = sum(a == b for a,b in zip(observed,truth["oracle"]))
            wrong = sum(a != b and a != "unknown" for a,b in zip(observed,truth["oracle"]))
            assert row["correct_answers"] == matches and row["wrong_answers"] == wrong
            assert row["unknown_answers"] == observed.count("unknown")
            if wrong:
                assert row["status"] == "incorrect"
            status = row["status"]
            wall_cap = plan.get("timeout_by_arm", {}).get(row["arm"], plan["timeout_s"])
            assert status in {"solved","timeout","unknown","error","incorrect"}
            if status == "solved":
                assert observed == truth["oracle"] and row["rc"] == 0
                assert 0 < row["wall_s"] <= wall_cap
            if status == "timeout":
                assert row["rc"] == -9 or row["wall_s"] > wall_cap or row.get("attempts")
            policy = plan.get("fallback_by_arm", {}).get(row["arm"])
            if policy is not None:
                trials = row["attempts"]
                assert 1 <= len(trials) <= 2
                if len(trials) == 2:
                    assert trials[0]["status"] == "timeout"
                    assert 0 < row["secondary_budget_s"] < wall_cap
                assert row["wall_s"] >= sum(t["wall_s"] for t in trials)
                assert row["answers"] == trials[-1]["answers"]
                for t in trials:
                    assert t["wrong_answers"] == sum(a != b and a != "unknown" for a,b in zip(t["answers"],truth["oracle"]))
            if status == "unknown":
                assert len(observed) == len(truth["oracle"]) and row["unknown_answers"] > 0
            assert row["par2"] == (row["wall_s"] if status == "solved" else 2*wall_cap)
            counts[row["arm"]][status] += 1
            sums[row["arm"]].append(row["par2"])
            row_count += 1
    assert row_count == len(inputs) * plan["repeats"] * len(counts)
    result={"programs":len(inputs),"repeats":plan["repeats"],"rows":row_count,
            "counts":counts,"par2":{arm:math.fsum(values)/len(values) for arm,values in sums.items()},
            "verified":["session SHA256","full permutations","answers","timeouts","PAR2","denominators"],
            "logs":{p.name:digest(p) for p in sorted(path.glob("worker-*.jsonl"))}}
    if publish:
        write_json(path / "VERIFY.json",result)
    return result

def launch(path, manifest, configs, repeats, seed, limit, cores=CORES, timeout=1.5,
           timeout_by_arm=None, solver_seed_by_arm=None, fallback_by_arm=None,
           manifest_by_arm=None, extra_flags_by_arm=None):
    check_budget()
    path.mkdir(parents=True, exist_ok=False)
    for params in configs.values():
        ev.config(params)
    for arm, policy in (fallback_by_arm or {}).items():
        assert arm in configs
        assert 0 < policy["first_s"] < (timeout_by_arm or {}).get(arm,timeout)
        ev.config(policy["second"])
    alternate={}
    for arm, other in (manifest_by_arm or {}).items():
        assert arm in configs
        alternate[arm]={"path":os.path.relpath(other,ROOT),"sha256":digest(other)}
    allowed_flags={"--arrays-exp","--arrays-eager-index","--no-arrays-optimize-linear"}
    for arm, extra in (extra_flags_by_arm or {}).items():
        assert arm in configs and len(set(extra))==len(extra) and set(extra)<=allowed_flags
    deadline = min(time.time() + limit, STOP - 8)
    write_json(path / "plan.json", {"created":now(), "manifest":os.path.relpath(manifest,ROOT),
               "manifest_sha256":digest(manifest), "configs":configs, "repeats":repeats, "seed":seed,
               "cores":list(cores)[:repeats], "timeout_s":timeout, "deadline":deadline,
               "timeout_by_arm":timeout_by_arm or {}, "solver_seed_by_arm":solver_seed_by_arm or {},
               "fallback_by_arm":fallback_by_arm or {}, "manifest_by_arm":alternate,
               "extra_flags_by_arm":extra_flags_by_arm or {}})
    cpus = load(path / "plan.json")["cores"]
    log(f"START {path.relative_to(ROOT)} · {repeats} повторов, {len(configs)} меток, CPU {cpus}")
    procs = []
    try:
        for slot in range(len(cpus)):
            with (path / f"worker-{slot}.stderr").open("wb") as out:
                procs.append(subprocess.Popen([PYTHON,"-B",__file__,"worker",path,str(slot)],
                                              stdout=out,stderr=out,stdin=subprocess.DEVNULL))
        while any(p.poll() is None for p in procs):
            check_budget()
            if time.time() > deadline + 5 or any(p.poll() not in (None,0) for p in procs):
                raise RuntimeError(f"replay incomplete: {[p.poll() for p in procs]}")
            time.sleep(3)
        if any(p.returncode for p in procs):
            raise RuntimeError(f"replay incomplete: {[p.returncode for p in procs]}")
        result = verify_replay(path)
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
            p.wait()
    log(f"VERIFY {path.relative_to(ROOT)} · {result['rows']} строк")
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["worker","verify"])
    parser.add_argument("path", type=Path)
    parser.add_argument("slot", type=int, nargs="?")
    args = parser.parse_args()
    if args.command == "worker":
        worker(args.path, args.slot)
    else:
        print(json.dumps(verify_replay(args.path),ensure_ascii=False))
