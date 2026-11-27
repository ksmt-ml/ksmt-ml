"""Release the precommitted test seed after the locks, then replay paired arms."""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT.parent / "campaign"
sys.path.insert(0, str(CAMPAIGN / "v1"))

import compare
from evaluator import TUNING_TIMEOUT, atomic_json, verify

SEEDS = (18713, 36251, 59077)
PAIRED_CORES = (8, 10, 12)
ARMS = ("default", "smac", "sol-auto", "sol-interactive")
TRAINING = CAMPAIGN / "dataset-v1"
OLD_FINAL = CAMPAIGN / "runs/campaign-v1/final"
USVM = CAMPAIGN / "v1/usvm"
compare.ARMS = ARMS[1:]


def read(path):
    return json.loads(path.read_text())


def rows(path):
    if path.exists():
        with path.open() as stream:
            yield from map(json.loads, stream)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def now():
    return datetime.now().astimezone().isoformat()


def stage(current, done, next_tasks):
    atomic_json(ROOT / "current.json", dict(updated=now(), current=current, done=done, next=next_tasks))


def gate(output, final):
    lock_file = output / "locks.json"
    locks = read(lock_file)["runs"]
    assert [r["seed"] for r in locks] == list(SEEDS)
    assert all(r["status"] == "ok" and r["config"] is not None for r in locks)
    for lock in locks:
        assert lock == read(output / f"seed-{lock['seed']}/locked.json")
        assert lock["wall_s"] <= 3601
    plan = read(ROOT / "heldout-commitment.json")
    seed = (ROOT / "private/heldout-seed").read_text().strip()
    assert seed != read(OLD_FINAL / "unsealed.json")["test_seed_hex"]
    assert hashlib.sha256(b"cvc5-interactive/test\0" + bytes.fromhex(seed)).hexdigest() == plan["commitment_sha256"]
    assert all(datetime.fromisoformat(plan["created"]) < datetime.fromisoformat(r["locked"]) for r in locks)
    record = {"locks_sha256": digest(lock_file), "commitment_sha256": plan["commitment_sha256"],
              "test_seed_hex": seed, "unlocked": now()}
    atomic_json(final / "unsealed.json", record)
    (final / "unsealed.json").chmod(0o444)
    return plan, seed, locks


def normalized_source(path):
    source = re.sub(rb"\bpublic class Program\d+\b", b"public class Program", path.read_bytes(), count=1)
    return hashlib.sha256(source).hexdigest()


def materialize(final, plan, seed):
    target = final / "test"
    prior = [TRAINING / "train", TRAINING / "validation", OLD_FINAL / "test"]
    exclude = [CAMPAIGN / "setup/usvm-pilot/programs.json", *(p / "programs.json" for p in prior)]
    python = [sys.executable, "-B"]
    commands = [
        [*python, str(USVM / "java_programs.py"), str(target), "test", "--seed", seed,
         "--count", str(plan["source_count"]), "--widths", ",".join(map(str, plan["widths"])),
         *(arg for path in exclude for arg in ("--exclude", str(path)))],
        [str(ROOT / "engine/trace.sh"), str(target), "10", str(plan["trace_budget_s"]),
         str(plan["trace_method_ms"]), str(plan["trace_z3_call_ms"])],
        [*python, str(USVM / "convert.py"), str(target)],
    ]
    with (final / "materialization.log").open("w") as log:
        for command in commands:
            log.write(json.dumps(command) + "\n")
            log.flush()
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=400, cwd=ROOT)
    prior_sessions = {r["sha256"] for path in prior for r in verify(path / "manifest.json")}
    prior_sources = {r["sha256"] for path in exclude for r in read(path)["programs"]}
    prior_normalized = {normalized_source(path.parent / r["source"])
                        for path in exclude for r in read(path)["programs"]}
    manifest = target / "manifest.json"
    data = read(manifest)
    accepted = []
    new_sources = {r["sha256"]: normalized_source(target / r["source"])
                   for r in read(target / "programs.json")["programs"]}
    for row in data["sessions"]:
        shape = new_sources[row["source_sha256"]]
        if row["sha256"] in prior_sessions or row["source_sha256"] in prior_sources or shape in prior_normalized:
            data["rejected"].append({"file": row["file"], "sha256": row["sha256"],
                                     "reason": "previous session or source duplicate"})
        else:
            accepted.append(row)
    data["sessions"] = accepted
    data["group_count"] = len({r["group"] for r in accepted})
    atomic_json(manifest, data)
    if data["group_count"] < plan["minimum_eligible_groups"]:
        raise RuntimeError(f"Only {data['group_count']} fresh programs passed source and trace checks")
    verify(manifest)
    atomic_json(final / "test-files.sha256.json", {str(p.relative_to(target)): digest(p)
                 for p in sorted(target.rglob("*")) if p.is_file()})
    (final / "test-files.sha256.json").chmod(0o444)
    for path in target.rglob("*"):
        if path.is_file():
            path.chmod(path.stat().st_mode & ~0o222)
    with (final / "z3-audit.log").open("wb") as log:
        subprocess.run([*python, str(USVM / "audit.py"), str(manifest), str(final / "z3-audit.jsonl"),
                        "--core", "14"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=520)
    return manifest


def paired_worker(manifest, lock_path, output, seed, core, repeats):
    records = read(lock_path)
    compare.rerun(manifest, records, output, seed, core, repeats, TUNING_TIMEOUT)


def pairwise(folder, manifest, repeats):
    by_cell = defaultdict(list)
    runs = {}
    all_status = Counter()
    for seed, core in zip(SEEDS, PAIRED_CORES, strict=True):
        block = folder / f"seed-{seed}"
        log = list(rows(block / "sessions.jsonl"))
        plan = read(block / "plan.json")
        summary = read(block / "summary.json")
        assert plan["paired_seed"] == seed and plan["core"] == core
        assert plan["repeats"] == repeats and set(plan["configs"]) == set(ARMS)
        assert plan["timeout_s"] == TUNING_TIMEOUT and Path(plan["test_manifest"]) == manifest
        expected = read(folder / f"locked-{seed}.json")
        assert plan["configs"] == {"default": {}, **{arm: r["config"] for arm, r in expected.items()}}
        items = verify(manifest)
        rng = random.Random(compare.ORDER_SEED ^ seed)
        assert len(log) == len(items) * repeats * len(ARMS)
        stream = iter(log)
        for repeat in range(repeats):
            rng.shuffle(items)
            for item in items:
                order = list(ARMS)
                rng.shuffle(order)
                for position, arm in enumerate(order):
                    row = next(stream)
                    assert (row["paired_seed"], row["repeat"], row["session"], row["group"],
                            row["position"], row["arm"]) == (seed, repeat, item["file"], item["group"], position, arm)
                    matched = sum(a == b for a, b in zip(row["answers"], item["oracle"]))
                    wrong = sum(a not in (b, "unknown") for a, b in zip(row["answers"], item["oracle"]))
                    assert row["correct_answers"] == matched and row["wrong_answers"] == wrong
                    assert row["queries"] == len(item["oracle"])
                    assert row["unknown_answers"] == row["answers"].count("unknown")
                    assert bool(wrong) == (row["status"] == "incorrect")
                    if row["status"] == "solved":
                        assert row["answers"] == item["oracle"] and row["par2"] == row["wall_s"]
                    else:
                        assert row["status"] in ("timeout", "unknown", "error", "incorrect")
                        assert row["par2"] == 2 * TUNING_TIMEOUT
                    by_cell[arm, seed, item["group"]].append(row["par2"])
                    all_status[row["status"]] += 1
        for arm in ARMS:
            selection = [r for r in log if r["arm"] == arm]
            assert abs(summary[arm]["par2"] - statistics.mean(r["par2"] for r in selection)) < 1e-12
            assert summary[arm]["correct_answers"] == sum(r["correct_answers"] for r in selection)
        runs[str(seed)] = len(log)

    groups = sorted({item["group"] for item in verify(manifest)})
    cell = {k: statistics.mean(value) for k, value in by_cell.items()}
    assert len(cell) == len(ARMS) * len(SEEDS) * len(groups)
    results = {}
    for reference in ("sol-auto", "smac", "default"):
        def differences(seeds, programs):
            ours = sum(cell["sol-interactive", s, g] for s in seeds for g in programs)
            base = sum(cell[reference, s, g] for s in seeds for g in programs)
            return ours / base, (ours - base) / (len(seeds) * len(programs))

        rng = random.Random(260918)
        programs_only, crossed = [], []
        for _ in range(5000):
            resample = rng.choices(groups, k=len(groups))
            programs_only.append(differences(SEEDS, resample))
            crossed.append(differences(rng.choices(SEEDS, k=len(SEEDS)), resample))
        ratio, delta = differences(SEEDS, groups)
        results[reference] = dict(
            ratio=ratio, delta_s=delta, program_ratio_ci_95=compare.interval([x[0] for x in programs_only]),
            program_seed_ratio_ci_95=compare.interval([x[0] for x in crossed]),
            program_seed_delta_s_ci_95=compare.interval([x[1] for x in crossed]),
            per_seed={str(seed): dict(zip(("ratio", "delta_s"), differences((seed,), groups), strict=True))
                      for seed in SEEDS})
    atomic_json(folder / "pairwise.json", dict(pairs=results, bootstrap_draws=5000,
                 bootstrap_seed=260918, seeds=SEEDS, program_count=len(groups)))
    return {"session_runs_by_seed": runs, "session_statuses": dict(all_status)}


def run(output, done):
    final = output / "final"
    final.mkdir()
    plan, seed, locks = gate(output, final)
    stage("Настройки зафиксированы. Записываю тестовые сеансы новых программ USVM.", done,
          ["Проверить трассы отдельным Z3.", "Измерить четыре метода в парном порядке и оценить интервалы."])
    try:
        manifest = materialize(final, plan, seed)
        kind = "fresh"
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as error:
        atomic_json(final / "fresh-error.json", {"error": repr(error), "time": now()})
        manifest = OLD_FINAL / "test/manifest.json"
        kind = "old-exploratory"
    repeats = plan["final_paired_repeats"]
    atomic_json(final / "cohort.json", {"kind": kind, "manifest": str(manifest), "sha256": digest(manifest)})

    comparison = final / "comparison"
    comparison.mkdir()
    stage("Измеряю четыре метода в случайном парном порядке, по три повтора.", done,
          ["Проверить сырые измерения и пересчитать интервалы по программам и seed."])
    workers = []
    try:
        for lock, core in zip(locks, PAIRED_CORES, strict=True):
            seed = lock["seed"]
            historical = read(OLD_FINAL / "comparison" / f"locked-{seed}.json")
            records = {arm: historical[arm] for arm in ("smac", "sol-auto")}
            records["sol-interactive"] = lock
            assert all(r["status"] == "ok" and r["seed"] == seed and r["config"] is not None for r in records.values())
            lock_path = comparison / f"locked-{seed}.json"
            atomic_json(lock_path, records)
            lock_path.chmod(0o444)
            command = [sys.executable, "-B", str(Path(__file__).resolve()), "worker", str(manifest),
                       str(lock_path), str(comparison / f"seed-{seed}"), str(seed), str(core), str(repeats)]
            with (comparison / f"seed-{seed}.log").open("wb") as log:
                workers.append(subprocess.Popen(command, stdout=log, stderr=log, cwd=ROOT))
        for worker in workers:
            if worker.wait(timeout=850):
                raise RuntimeError(f"comparison worker failed: {worker.returncode}")
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait()
    verification = pairwise(comparison, manifest, repeats)
    compare.report(comparison)
    if kind == "fresh":
        for name, expected in read(final / "test-files.sha256.json").items():
            assert digest(manifest.parent / name) == expected
        assert datetime.fromisoformat(read(final / "unsealed.json")["unlocked"]).timestamp() <= (
            (manifest.parent / "programs.json").stat().st_mtime)
    verification.update(cohort=kind, manifest_sha256=digest(manifest), locks_sha256=digest(output / "locks.json"),
                        oracle_audit_statuses=dict(Counter(r["status"] for r in rows(final / "z3-audit.jsonl"))))
    atomic_json(final / "VERIFY.json", verification)
    return kind


if __name__ == "__main__":
    if len(sys.argv) == 8 and sys.argv[1] == "worker":
        paired_worker(*map(Path, sys.argv[2:5]), *map(int, sys.argv[5:]))
    else:
        raise SystemExit("internal entry point: worker MANIFEST LOCK_PATH OUTPUT SEED CORE REPEATS")
