"""Independently cross-check completed locks, held-out logs, and file digests."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics


def read(p):
    return json.loads(p.read_text())


def digest(p):
    with p.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def verify(run):
    full_lock = run / "full/locks.json"
    full = read(full_lock)
    smoke = read(run / "smoke/locks.json")
    assert smoke["expected_runs"] == smoke["actual_runs"] == 5 and smoke["ok"]
    assert full["expected_runs"] == full["actual_runs"] == 12
    final = run / "final"
    unsealed = read(final / "unsealed.json")
    assert digest(full_lock) == unsealed["locks_sha256"]
    seed = bytes.fromhex(unsealed["test_seed_hex"])
    assert hashlib.sha256(b"cvc5-campaign-v1/test\0" + seed).hexdigest() == unsealed["commitment_sha256"]
    assert all(datetime.fromisoformat(r["locked"]) < datetime.fromisoformat(unsealed["unlocked"])
               for r in full["runs"] if "locked" in r)
    for name, expected in read(final / "test-files.sha256.json").items():
        assert digest(final / "test" / name) == expected, name
    manifest = read(final / "test/manifest.json")
    lookup = {row["file"]: row["oracle"] for row in manifest["sessions"]}
    assert manifest["group_count"] == len({r["group"] for r in manifest["sessions"]})
    report = read(final / "comparison/comparison.json")
    result = {}
    combined = Counter()
    for block in sorted((final / "comparison").glob("seed-*")):
        if not block.is_dir():
            continue
        plan = read(block / "plan.json")
        summary = read(block / "summary.json")
        runs = [json.loads(line) for line in (block / "sessions.jsonl").read_text().splitlines()]
        assert len(runs) == plan["repeats"] * len(plan["configs"]) * len(lookup)
        combos = {(r["arm"], r["repeat"], r["session"]) for r in runs}
        assert len(combos) == len(runs)
        for row in runs:
            expected = lookup[row["session"]]
            count = sum(a == b for a, b in zip(row["answers"], expected))
            wrong = sum(a != b and a != "unknown" for a, b in zip(row["answers"], expected))
            assert row["queries"] == len(expected)
            assert row["correct_answers"] == count and row["wrong_answers"] == wrong
            assert bool(wrong) == (row["status"] == "incorrect")
            if row["status"] == "solved":
                assert row["answers"] == expected and row["par2"] == row["wall_s"]
            else:
                assert row["par2"] == 2 * plan["timeout_s"]
            combined[row["status"]] += 1
        for arm, info in summary.items():
            rows = [row for row in runs if row["arm"] == arm]
            assert len(rows) == len(lookup) * plan["repeats"]
            assert abs(statistics.mean(r["par2"] for r in rows) - info["par2"]) < 1e-12
            assert info["correct_answers"] == sum(r["correct_answers"] for r in rows)
            assert info["wrong_answers"] == sum(r["wrong_answers"] for r in rows)
        result[str(plan["paired_seed"])] = {"arms": sorted(summary), "session_runs": len(runs)}
    for arm, info in report["arms"].items():
        assert info["paired_seed_count"] == sum(arm in r["arms"] for r in result.values())
    hashes = {}
    for directory in (run / "smoke", run / "full", final / "comparison"):
        for f in sorted(directory.rglob("*")):
            if f.is_file() and not f.is_symlink() and not {"state", "responses", "requests", "tmp"} & set(f.parts):
                if f.suffix in (".json", ".jsonl", ".stderr", ".log", ".py", ".txt"):
                    hashes[str(f.relative_to(run))] = digest(f)
    with (run / "VERIFY.json").open("x") as f:
        f.write(json.dumps({"full_gate_ok": full["ok"], "paired_seeds": result,
                           "heldout_programs": manifest["group_count"],
                           "heldout_statuses": dict(combined),
                           "measurement_artifact_sha256": hashes}, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    verify(p.parse_args().run)
