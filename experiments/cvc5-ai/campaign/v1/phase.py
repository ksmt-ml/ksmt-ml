"""Launch isolated, paired, finite tuning waves and preserve immediate results."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

from evaluator import ROOT, SOLVER, TUNING_TIMEOUT, atomic_json, json_line, verify
from tune import ARMS


SEEDS = (18713, 36251, 59077)
SMOKE_SEED = 91573
CORES = (8, 9, 10, 11, 12, 13)
SCRIPT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset-v1"
PROTOCOL = ROOT / "protocol-v1"
DONE = [
    "Проверены cvc5 1.4.1 и SMAC 2.4.0.",
    "Собран закреплённый USVM, записаны сеансы новых Java-программ и ответы Z3.",
    "Измерены сложность и шум. Проверена изоляция вложенного Sol.",
]


def state(current, done, next_tasks):
    atomic_json(ROOT / "current.json", {
        "current": current, "done": done, "next": next_tasks,
        "updated": datetime.now(timezone.utc).isoformat(),
    })


def digest(p):
    return hashlib.file_digest(p.open("rb"), "sha256").hexdigest()


def artifact_paths():
    for folder in (SCRIPT, PROTOCOL):
        yield from (p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    yield DATASET / "test-seal.json"
    yield ROOT / "setup/usvm-pilot/programs.json"
    yield SOLVER
    for name in ("requirements-z3.lock", "usvm-smt-logging.patch", "usvm-client-instrumentation.patch",
                 "usvm-classpath.env"):
        yield ROOT / "setup" / name
    for scope in ("train", "validation"):
        folder = DATASET / scope
        yield folder / "manifest.json"
        yield folder / "programs.json"
        for relative in ("sessions/*.smt2", "source/pilot/*.java", "traces/*"):
            yield from (p for p in folder.glob(relative) if p.is_file())


def seal():
    freeze = DATASET / "frozen.json"
    if freeze.exists():
        raise FileExistsError(f"Refusing to overwrite prior freeze {freeze}")
    train = verify(DATASET / "train/manifest.json")
    validation = verify(DATASET / "validation/manifest.json")
    if len({x["group"] for x in train}) < 10 or len({x["group"] for x in validation}) < 6:
        raise ValueError("too few training or validation groups after oracle-only admission")
    if {x["sha256"] for x in train} & {x["sha256"] for x in validation}:
        raise ValueError("identical session across the training and validation splits")
    programs = [json.loads((DATASET / scope / "programs.json").read_text())["programs"]
                for scope in ("train", "validation")]
    if {x["sha256"] for x in programs[0]} & {x["sha256"] for x in programs[1]}:
        raise ValueError("identical source program across train and validation")
    items = {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(artifact_paths()))}
    atomic_json(freeze, {"format": 1, "files_sha256": items, "session_timeout_s": TUNING_TIMEOUT,
                        "search_seeds": SEEDS, "smoke_seed": SMOKE_SEED, "cores": CORES,
                        "full_budget_s": 3600, "full_validation_reserve_s": 180,
                        "heldout": {"private_seed_commitment": "run-specific test-seal.json",
                                    "source_count": 27, "widths": [5, 7, 9, 11, 12],
                                    "trace_method_ms": 5000, "trace_z3_call_ms": 1800,
                                    "trace_budget_s": 330, "final_paired_repeats": 3,
                                    "minimum_eligible_groups": 15,
                                    "selection": "all balanced transcripts with known oracle; exclude exact session overlap"},
                        "created": datetime.now(timezone.utc).isoformat()})
    for name in items:
        p = ROOT / name
        p.chmod(p.stat().st_mode & ~0o222)
    freeze.chmod(0o444)
    print(f"frozen {len(items)} files at {freeze}", flush=True)


def verify_freeze():
    frozen = json.loads((DATASET / "frozen.json").read_text())
    for name, expected in frozen["files_sha256"].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f"frozen artifact changed: {name}")
    return frozen


def waves(stage):
    if stage == "smoke":
        return [[(SMOKE_SEED, name) for name in (*ARMS, "default")]]
    s0, s1, s2 = SEEDS
    return [[*((s0, arm) for arm in ARMS), (s1, "random"), (s1, "smac")],
            [(s1, "sol-smac"), (s1, "sol-auto"), *((s2, arm) for arm in ARMS)]]


def read_outcomes(output):
    return [json.loads(p.read_text()) for p in sorted(output.glob("seed-*/*/locked.json"))]


def progress(output, stage, wave, all_jobs):
    rows = []
    for seed, arm, core in all_jobs:
        directory = output / f"seed-{seed}" / arm
        final = directory / "locked.json"
        status = directory / "progress.json"
        row = {"seed": seed, "arm": arm, "core": core}
        if final.exists():
            row.update(json.loads(final.read_text()))
        elif status.exists():
            row.update({"status": "running", **json.loads(status.read_text())})
        else:
            row.update({"status": "pending"})
        rows.append(row)
    atomic_json(output / "progress.json", {"stage": stage, "wave": wave, "runs": rows})
    finished = sum(x["status"] in ("ok", "partial") for x in rows)
    samples = sum(x.get("candidate_count", x.get("candidates", 0)) for x in rows)
    done = DONE + ["Манифесты, оценщик и протокол заморожены по SHA-256."]
    if stage == "full":
        done += ["Пробные запуски всех методов завершены."]
    label = "Пробное сравнение" if stage == "smoke" else f"Основное сравнение, волна {wave}/2"
    state(f"{label}. Зафиксировано запусков {finished}/{len(rows)}, измерено кандидатов {samples}.",
          done, ["Завершить измерения и зафиксировать настройки.",
                 "Открыть закрытый тест только после фиксации всех настроек."])


def launch(stage, output):
    frozen = verify_freeze()
    output.mkdir(exist_ok=False, parents=True)
    budget = 210 if stage == "smoke" else frozen["full_budget_s"]
    reserve = 65 if stage == "smoke" else frozen["full_validation_reserve_s"]
    repeats = 1 if stage == "smoke" else 2
    call_timeout = 90 if stage == "smoke" else 300
    schedule = []
    for wave_number, group in enumerate(waves(stage), 1):
        available = list(CORES)
        random.Random(2230443 + wave_number).shuffle(available)
        schedule.append([(seed, arm, core) for (seed, arm), core in zip(group, available)])
    all_jobs = [job for wave in schedule for job in wave]
    if any(core not in os.sched_getaffinity(0) for _, _, core in all_jobs):
        raise ValueError("CPU affinity differs from the frozen host plan")
    atomic_json(output / "plan.json", {
        "stage": stage, "budget_s": budget, "validation_reserve_s": reserve,
        "validation_repeats": repeats, "tuning_seeds": sorted({job[0] for job in all_jobs}),
        "default": "untuned on held-out; smoke measures default training and validation once",
        "schedule": schedule, "max_parallel_solver_workers": max(map(len, schedule)),
        "freeze_sha256": digest(DATASET / "frozen.json")})
    for wave_number, wave in enumerate(schedule, 1):
        active = []
        begin = time.monotonic()
        try:
            for seed, arm, core in wave:
                directory = output / f"seed-{seed}" / arm
                directory.parent.mkdir(parents=True, exist_ok=True)
                command = [sys.executable, "-B", str(SCRIPT / "tune.py"), arm, str(directory),
                           "--train", str(DATASET / "train/manifest.json"),
                           "--validation", str(DATASET / "validation/manifest.json"),
                           "--protocol", str(PROTOCOL), "--seed", str(seed), "--core", str(core),
                           "--budget", str(budget), "--validation-seconds", str(reserve),
                           "--validation-repeats", str(repeats), "--call-timeout", str(call_timeout),
                           "--begin", str(begin)]
                env = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                       "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}
                with (directory.parent / f"{arm}.controller.log").open("wb") as logs:
                    p = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=logs, stderr=logs,
                                         cwd=ROOT, env=env)
                active.append((p, directory))
                json_line(output / "scheduler.jsonl", {"event": "start", "wave": wave_number,
                          "arm": arm, "seed": seed, "core": core, "monotonic": time.monotonic()})
            while any(p.poll() is None for p, _ in active) and time.monotonic() < begin + budget + 8:
                progress(output, stage, wave_number, all_jobs)
                time.sleep(5)
        finally:
            for p, _ in active:
                if p.poll() is None:
                    p.kill()
                p.wait()
        for p, directory in active:
            if not (directory / "locked.json").exists():
                directory.mkdir(exist_ok=True)
                atomic_json(directory / "locked.json", {"arm": directory.name,
                    "seed": int(directory.parent.name.removeprefix("seed-")), "config": None,
                    "status": "partial", "error": f"controller stopped with exit code {p.returncode}"})
            json_line(output / "scheduler.jsonl", {"event": "end", "wave": wave_number,
                      "directory": str(directory.relative_to(output)), "rc": p.returncode,
                      "monotonic": time.monotonic()})
        progress(output, stage, wave_number, all_jobs)
    rows = read_outcomes(output)
    result = {"expected_runs": len(all_jobs), "actual_runs": len(rows),
              "ok": len(rows) == len(all_jobs) and all(r["status"] == "ok" for r in rows), "runs": rows}
    atomic_json(output / "locks.json", result)
    (output / "locks.json").chmod(0o444)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("freeze", "smoke", "full"))
    p.add_argument("output", type=Path, nargs="?")
    a = p.parse_args()
    if a.action == "freeze":
        seal()
    elif a.output is None:
        p.error("provide a new output directory")
    else:
        launch(a.action, a.output.resolve())


if __name__ == "__main__":
    main()
