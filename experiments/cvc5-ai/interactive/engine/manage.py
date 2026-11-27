"""Run the preflight-gated interactive campaign and preserve status/replay artifacts."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import traceback

from evaluate import (CAMPAIGN, ROOT, SEEDS, TRAINING, atomic_json, digest,
                      now, read, rows, run as evaluate, stage)
from evaluate import verify

CORES = (10, 11, 12)
DONE = [
    "Проверены неизменяемые файлы кампании, cvc5 1.4.1 и однопоточный оценщик.",
    "Независимый тестовый seed зафиксирован по SHA-256 до настройки.",
]
SCRIPT = ROOT / "engine/interactive.py"


def frozen(output):
    manifest = TRAINING / "frozen.json"
    original = read(manifest)
    for name, expected in original["files_sha256"].items():
        assert digest(CAMPAIGN / name) == expected, name
    for name in ("options.json", "evaluator.py"):
        assert digest(ROOT / "model-protocol" / name) == digest(CAMPAIGN / "protocol-v1" / name)
    commands = {
        "cvc5": [str(CAMPAIGN / "setup/cvc5/cvc5-Linux-x86_64-static/bin/cvc5"), "--version"],
        "codex": [str(Path.home() / ".local/bin/codex"), "--version"],
        "cpu": ["lscpu", "-e"],
        "java": ["/usr/lib/jvm/java-21-openjdk/bin/java", "-version"],
        "bwrap": ["bwrap", "--version"],
    }
    diagnostics = {}
    for name, argv in commands.items():
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10, cwd=ROOT)
        diagnostics[name] = {"status": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        assert result.returncode == 0, name
    assert "cvc5 1.4.1" in diagnostics["cvc5"]["stdout"]
    assert set(CORES) <= os.sched_getaffinity(0)
    sibling_lists = {core: Path(f"/sys/devices/system/cpu/cpu{core}/topology/thread_siblings_list")
                     .read_text().strip() for core in CORES}
    assert len(set(sibling_lists.values())) == len(CORES)
    cgroup = Path("/sys/fs/cgroup") / Path(
        Path("/proc/self/cgroup").read_text().splitlines()[0].split(":", 2)[2]).relative_to("/")
    control_names = ("cpu.max", "cpu.stat", "memory.max", "memory.events", "pids.max", "cpuset.cpus.effective")
    diagnostics.update(cgroup=str(cgroup), resources={
        name: (cgroup / name).read_text() for name in control_names if (cgroup / name).exists()},
        affinity=sorted(os.sched_getaffinity(0)), siblings=sibling_lists,
        rlimits={name: resource.getrlimit(getattr(resource, name)) for name in ("RLIMIT_AS", "RLIMIT_NPROC")})
    atomic_json(output / "resources.json", diagnostics)
    files = [*sorted((ROOT / "engine").glob("*.*")), *sorted((ROOT / "model-protocol").glob("*.*")),
             ROOT / "heldout-commitment.json", ROOT / "run-experiment.sh"]
    atomic_json(output / "plan.json", dict(
        created=now(), budget_s=3600, validation_reserve_s=180, validation_repeats=2,
        solver_seed=18473, solver_workers_per_arm=1, parallel_workers=3, search_cores=dict(zip(SEEDS, CORES)),
        original_freeze_sha256=digest(manifest),
        implementation_sha256={str(p.relative_to(ROOT)): digest(p) for p in files}))


def checked_run(directory, full):
    lock = read(directory / "locked.json")
    record = read(directory / "run.json")
    trace = list(rows(directory / "model-events.jsonl"))
    starts = [r for r in trace if r["event"] == "start"]
    ends = [r for r in trace if r["event"] == "end"]
    assert lock["arm"] == "sol-interactive"
    assert lock["status"] == "ok", lock["error"]
    assert len(starts) == len(ends) == lock["model_calls"] and len(ends) >= 2
    budget = 3600 if full else 300
    reserve = 180 if full else 65
    assert record["budget_s"] == budget and record["validation_reserve_s"] == reserve
    assert record["validation_repeats"] == (2 if full else 1)
    if full:
        prior = read(CAMPAIGN / "runs/campaign-v1/full" / f"seed-{lock['seed']}/sol-auto/run.json")
        for key in ("budget_s", "validation_reserve_s", "validation_repeats", "workers",
                    "session_timeout_s", "train_groups_in_order"):
            assert record[key] == prior[key], key
    assert budget - reserve <= lock["wall_s"] <= budget + 1
    assert starts[-1]["start_s"] + ends[-1]["elapsed_s"] >= budget - reserve - 2
    assert any(e["resume_thread_id"] and e["completed_trials"] for e in ends)
    successes = []
    for start, end in zip(starts, ends, strict=True):
        assert start["turn"] == end["turn"]
        stamp = start["clock_at_prompt"]
        prompt = directory / "model" / f"turn-{start['turn']:03}.prompt.txt"
        wording = prompt.read_text()
        intro = "real 60-minute (3,600-second)" if full else "real 300-second practice"
        assert f"You own a {intro} wall-clock budget" in wording
        assert f"validation reserve of {reserve} seconds" in wording
        assert f"Actual elapsed wall time: {stamp['elapsed_wall_s']:.2f} s." in wording
        assert f"Actual remaining search time: {stamp['search_remaining_s']:.2f} s." in wording
        assert abs(stamp["elapsed_wall_s"] + stamp["search_remaining_s"] - (budget - reserve)) < 0.03
        assert abs(stamp["elapsed_wall_s"] - start["start_s"]) < 1
        if start["turn"] == 0:
            assert "Choose your own time allocation" in wording and "initial plan" in wording
        else:
            assert f"Live continuation {start['turn'] + 1}" in wording
        for event in rows(prompt.with_name(prompt.name.replace(".prompt.txt", ".jsonl"))):
            item = event.get("item", {})
            assert item.get("type") not in {"collab_tool_call", "agent_spawn", "spawn_agent"}
            if event.get("type") == "item.completed" and item.get("type") == "command_execution":
                output = item.get("aggregated_output", "")
                if item.get("exit_code") == 0 and "TRAIN_ONLY_OK" in output.splitlines():
                    successes.append(start["turn"])
    assert successes, "No successful model-visible isolation probe"
    trials = list(rows(directory / "candidates.jsonl"))
    sessions = list(rows(directory / "train-sessions.jsonl"))
    candidates = {r["candidate_id"]: r for r in trials}
    assert all(s["candidate_id"] in candidates for s in sessions)
    assert all(r["finished_s"] <= budget - reserve + 0.2 for r in trials)
    assert sum(not r["counts"]["budget"] for r in trials) == lock["candidate_count"]
    unique_feedback = {}
    for event in rows(directory / "broker.jsonl"):
        response = event["response"]
        trial = response.get("trial")
        if trial and not trial.get("cached_observation"):
            ident = trial["candidate_id"]
            assert ident not in unique_feedback and candidates[ident] == trial
            assert response["sessions"] and "wall_s" in response["sessions"][0]
            unique_feedback[ident] = event["turn"]
    lock_candidates = sorted((r for r in trials if r["eligible"]), key=lambda r: (r["par2"], r["candidate_id"]))[:3]
    default = trials[0]
    if default["eligible"] and all(r["config"] for r in lock_candidates):
        lock_candidates.append(default)
    assert default["config"] == {} and default["origin"] == "default"
    assert [x["candidate_id"] for x in lock["validation"]] == [x["candidate_id"] for x in lock_candidates]
    selected = min((r for r in lock["validation"] if r["eligible"]),
                   key=lambda r: (r["par2"], bool(r["config"]), r["candidate_id"]))
    assert selected["config"] == lock["config"]
    assert set(unique_feedback) == {r["candidate_id"] for r in trials if r["origin"] == "sol-interactive"}
    return lock, {
        "clock_snapshots_verified": len(starts), "isolation_pass_turns": successes,
        "unique_feedback_trials": len(unique_feedback), "trial_turns": sorted(set(unique_feedback.values())),
        "training_session_statuses": dict(Counter(s["status"] for s in sessions)),
        "completion_elapsed_s": starts[-1]["start_s"] + ends[-1]["elapsed_s"],
        "turn_count": len(ends),
    }


def pulse(output, done):
    results = {}
    for seed in SEEDS:
        path = output / f"seed-{seed}/progress.json"
        if path.exists():
            record = read(path)
            results[seed] = dict(candidates=record["candidates"], elapsed_s=record["elapsed_s"],
                                 calls=record["calls"])
    atomic_json(output / "progress.json", {"updated": now(), "seeds": results})
    summaries = ", ".join(f"{seed}: {value['candidates']} кандидатов, {value['elapsed_s'] / 60:.1f} мин"
                          for seed, value in results.items())
    stage(f"Идут три независимых 60-минутных поиска. {summaries}", done,
          ["Зафиксировать выбор после автоматической валидации.",
           "Раскрыть seed и выполнить закрытую оценку."])


def tune(output, done):
    workers = []
    begin = time.monotonic()
    try:
        for seed, core in zip(SEEDS, CORES, strict=True):
            command = [sys.executable, "-B", str(SCRIPT), str(output / f"seed-{seed}"),
                       "--seed", str(seed), "--core", str(core), "--begin", str(begin)]
            with (output / f"seed-{seed}.log").open("wb") as log:
                p = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                     env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                                          "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"})
                workers.append(p)
        while any(p.poll() is None for p in workers) and time.monotonic() < begin + 3610:
            pulse(output, done)
            time.sleep(5)
        assert all(p.poll() == 0 for p in workers), [p.poll() for p in workers]
    finally:
        for p in workers:
            if p.poll() is None:
                p.kill()
            p.wait()
    pulse(output, done)
    locks = []
    verification = {}
    for seed in SEEDS:
        lock, diagnostics = checked_run(output / f"seed-{seed}", full=True)
        locks.append(lock)
        verification[seed] = diagnostics
    atomic_json(output / "tuning-verify.json", verification)
    atomic_json(output / "locks.json", {"ok": True, "runs": locks})
    (output / "locks.json").chmod(0o444)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--smoke", required=True, type=Path)
    args = p.parse_args()
    output = args.output.resolve()
    smoke = args.smoke.resolve()
    output.relative_to(ROOT / "runs")
    smoke.relative_to(ROOT / "runs")
    output.mkdir(parents=True)
    done = DONE.copy()
    try:
        frozen(output)
        lock, verified = checked_run(smoke, full=False)
        assert len(verified["trial_turns"]) >= 2
        atomic_json(output / "smoke-gate.json", {"directory": str(smoke), "lock": lock, "verified": verified})
        done.append("Пробный поиск подтвердил изоляцию, реальные измерения, продолжение и соблюдение срока.")
        tune(output, done)
        done.append("Три часовых поиска завершены. Победители валидации и бюджет проверены.")
        kind = evaluate(output, done)
        done.append("Сырые оценки, случайный порядок, ответы и парный bootstrap проверены.")
        stage("Закрытая оценка завершена." if kind == "fresh" else
              "Завершена разведочная оценка на старом тестовом наборе.", done, [])
    except Exception as error:
        (output / "failure.txt").write_text(traceback.format_exc())
        stage(f"Выполнение прервано: {type(error).__name__}. Подробности в {output.name}/failure.txt.",
              done, ["Проверить причину остановки; сохранены полученные логи и измерения."])
        raise


if __name__ == "__main__":
    main()
