"""Unlock the precommitted test seed only after complete tuning lock records."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from evaluator import ROOT, atomic_json, verify
from phase import DATASET, DONE, SCRIPT, SEEDS, digest, state, verify_freeze
from tune import ARMS


def run(output, seed_hex):
    frozen = verify_freeze()
    full = output / "full"
    locks_path = full / "locks.json"
    locks = json.loads(locks_path.read_text())
    records = {(r["seed"], r["arm"]): r for r in locks["runs"]}
    expected = {(seed, arm) for seed in SEEDS for arm in ARMS}
    if records.keys() != expected or any(r["status"] not in ("ok", "partial") for r in records.values()):
        raise ValueError("held-out release requires a lock for every scheduled arm")
    seal = json.loads((DATASET / "test-seal.json").read_text())
    seed_hex = seed_hex.strip()
    raw = bytes.fromhex(seed_hex)
    if len(raw) != 16 or hashlib.sha256(b"cvc5-campaign-v1/test\0" + raw).hexdigest() != seal["commitment_sha256"]:
        raise ValueError("incorrect held-out seed for pre-run commitment")
    out = output / "final"
    out.mkdir(exist_ok=False)
    gate = {
        "locks_sha256": digest(locks_path), "test_seed_hex": seed_hex,
        "commitment_sha256": seal["commitment_sha256"],
        "unlocked": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(out / "unsealed.json", gate)
    (out / "unsealed.json").chmod(0o444)
    done = DONE + ["Манифесты и оценщик заморожены.", "Пробные запуски завершены.",
                   "Все основные запуски остановлены, итоговые настройки зафиксированы."]
    state("Открыт закрытый seed. Записываю тестовые сеансы новых Java-программ USVM.", done,
          ["Провести парные повторные измерения.", "Подсчитать PAR2, ответы и интервалы неопределённости."])
    test_dir = out / "test"
    plan = frozen["heldout"]
    with (out / "materialization.log").open("w") as log:
        for command in (
            [sys.executable, "-B", str(SCRIPT / "usvm/java_programs.py"), str(test_dir), "test",
             "--seed", seed_hex, "--count", str(plan["source_count"]),
             "--widths", ",".join(map(str, plan["widths"])),
             "--exclude", str(ROOT / "setup/usvm-pilot/programs.json"),
             "--exclude", str(DATASET / "train/programs.json"),
             "--exclude", str(DATASET / "validation/programs.json")],
            [str(SCRIPT / "usvm/trace.sh"), str(test_dir), "10", str(plan["trace_budget_s"]),
             str(plan["trace_method_ms"]), str(plan["trace_z3_call_ms"])],
            [sys.executable, "-B", str(SCRIPT / "usvm/convert.py"), str(test_dir)],
        ):
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    # The only post-capture removals use oracle/trace completeness and exact
    # overlap. They do not consult any cvc5 timings or candidate outcomes.
    test_manifest = test_dir / "manifest.json"
    train_and_val = [item for part in ("train", "validation")
                     for item in verify(DATASET / part / "manifest.json")]
    prior_sessions = {item["sha256"] for item in train_and_val}
    prior_sources = {item["source_sha256"] for item in train_and_val}
    m = json.loads(test_manifest.read_text())
    accepted, removed = [], []
    for item in m["sessions"]:
        if item["sha256"] in prior_sessions or item["source_sha256"] in prior_sources:
            removed.append({"session": item["file"], "sha256": item["sha256"], "reason": "exact overlap"})
        else:
            accepted.append(item)
    m["sessions"] = accepted
    m["rejected"].extend(removed)
    m["group_count"] = len({item["group"] for item in accepted})
    atomic_json(test_manifest, m)
    if m["group_count"] < plan["minimum_eligible_groups"]:
        raise RuntimeError(f"test corpus has too few admitted programs: {m['group_count']}")
    verify(test_manifest)
    atomic_json(out / "test-files.sha256.json", {
        str(p.relative_to(test_dir)): digest(p) for p in sorted(test_dir.rglob("*")) if p.is_file()})
    for p in test_dir.rglob("*"):
        if p.is_file():
            p.chmod(p.stat().st_mode & ~0o222)
    (out / "test-files.sha256.json").chmod(0o444)
    state(f"Начаты парные тестовые измерения по {m['group_count']} Java-программам.", done,
          ["Завершить повторные измерения.", "Подсчитать интервалы по программам и seed."])
    subprocess.run([sys.executable, "-B", str(SCRIPT / "usvm/audit.py"), str(test_manifest),
                    str(out / "z3-audit.jsonl"), "--core", "14"], check=True, timeout=520)
    comparison = out / "comparison"
    comparison.mkdir()
    workers = []
    try:
        for seed, core in zip(SEEDS, (8, 10, 12)):
            selected = {arm: records[seed, arm] for arm in ARMS}
            src = comparison / f"locked-{seed}.json"
            atomic_json(src, selected)
            src.chmod(0o444)
            command = [sys.executable, "-B", str(SCRIPT / "compare.py"), "rerun", str(test_manifest),
                       str(src), str(comparison / f"seed-{seed}"), "--seed", str(seed),
                       "--core", str(core), "--repeats", str(plan["final_paired_repeats"])]
            with (comparison / f"seed-{seed}.log").open("wb") as log:
                workers.append(subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log))
        since = time.monotonic()
        while any(p.poll() is None for p in workers) and time.monotonic() - since < 850:
            time.sleep(4)
        if any(p.poll() != 0 for p in workers):
            raise RuntimeError("one or more paired comparison workers did not finish")
    finally:
        for p in workers:
            if p.poll() is None:
                p.kill()
            p.wait()
    subprocess.run([sys.executable, "-B", str(SCRIPT / "compare.py"), "report", str(comparison)],
                   check=True, timeout=75)
    state("Кампания и закрытая оценка завершены." if locks["ok"] else
          "Закрытая оценка завершена для доступных настроек. Часть запусков осталась неполной.",
          done + ["Записаны результаты парной закрытой оценки и интервалы неопределённости."], [])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    run(args.output.resolve(), sys.stdin.readline())
