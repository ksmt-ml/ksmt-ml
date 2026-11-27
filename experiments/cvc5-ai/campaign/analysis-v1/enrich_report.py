"""Append diagnostics and optional analyses to the completed result report."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import statistics


def read(path):
    return json.loads(path.read_text())


def rows(path):
    if path.is_file():
        for line in path.read_text().splitlines():
            yield json.loads(line)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    a = p.parse_args()
    run = a.run
    root = Path(__file__).resolve().parents[1]
    dst = root / "RESULTS.md"
    body = dst.read_text().split("\n## Final diagnostics\n", 1)[0].rstrip()
    full = read(run / "full/locks.json")
    audit = list(rows(run / "final/z3-audit.jsonl"))
    audit_counts = Counter(r["status"] for r in audit)
    audit_queries = sum(r["observed"] for r in audit if r["status"] == "ok")
    start = datetime.fromisoformat(read(root / "preparation.json")["started"])
    freeze = datetime.fromisoformat(read(root / "dataset-v1/frozen.json")["created"])
    preparation = (freeze - start).total_seconds() / 60
    by_arm = defaultdict(Counter)
    errors = Counter()
    missing_usage = Counter()
    for lock in full["runs"]:
        directory = run / "full" / f"seed-{lock['seed']}" / lock["arm"]
        arm = lock["arm"]
        by_arm[arm].update(row["status"] for row in rows(directory / "train-sessions.jsonl"))
        for row in rows(directory / "train-sessions.jsonl"):
            if row["status"] == "error":
                message = row.get("stderr", "") + "\n" + row.get("stdout", "")
                reason = ("ITE simplification with unsat cores" if "ITE simp not supported with unsat cores" in message
                          else next((line for line in message.splitlines() if line.strip()), "solver error"))
                errors[reason[:200]] += 1
        for row in rows(directory / "model-events.jsonl"):
            if row["event"] == "end" and row["kind"] != "policy" and not row["usage"]:
                missing_usage[arm] += 1

    lines = ["", "## Final diagnostics", "",
             f"Corpus and evaluator freeze took {preparation:.1f} minutes from the preparation start. "
             f"The independent final Z3 audit fully confirmed {audit_queries} answers across "
             f"{audit_counts['ok']}/{len(audit)} sessions. Audit outcomes and raw rows are under the final run directory.",
             "", "| Arm | Training sessions solved | Timeout | Unknown | Error | Incorrect | Cut short by budget |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for arm in sorted(by_arm):
        c = by_arm[arm]
        fields = (c[x] for x in ("solved", "timeout", "unknown", "error", "incorrect", "budget"))
        lines.append(f"| {arm} | " + " | ".join(map(str, fields)) + " |")
    if errors:
        if errors["ITE simplification with unsat cores"]:
            lines += ["", f"{errors['ITE simplification with unsat cores']} training errors came from the "
                      "ITE simplification option conflicting with unsat core production. The singleton "
                      "calibration probe used a short session without this operation. The frozen search "
                      "space therefore included a choice invalid for part of the corpus. Arms sampled "
                      "it at different rates, which changes the cost of searching the shared space."]
        for cause, count in errors.items():
            if cause != "ITE simplification with unsat cores":
                lines += ["", f"{count} training errors · {cause}."]
    if missing_usage:
        lines += ["", "Sol did not emit completion-token telemetry for "
                  + ", ".join(f"{count} {arm} call" + ("s" if count != 1 else "")
                              for arm, count in missing_usage.items())
                  + ". Their call counts and wall time are recorded. Token totals in the run table include "
                    "only calls that emitted usage."]
    pairwise = run / "final/comparison/pairwise.json"
    if pairwise.is_file():
        rows_of_interest = {(a, b) for a, b in (("sol-smac", "smac"), ("sol-auto", "smac"), ("smac", "random"))}
        lines += ["", "### Direct held-out comparisons", "",
                  "| Arm / reference | PAR2 ratio | 95% program + seed interval | Common seeds |",
                  "|---|---:|---|---:|"]
        for row in read(pairwise)["pairs"]:
            if (row["arm"], row["reference"]) in rows_of_interest:
                low, high = row["program_seed_ci_95"]
                lines.append(f"| {row['arm']} / {row['reference']} | {row['ratio']:.3f} | "
                             f"{low:.3f} to {high:.3f} | {len(row['seeds'])} |")
    ablations = run / "ABLATIONS.json"
    if ablations.is_file():
        data = read(ablations)
        lines += ["", "### Training-only offline ablations", "",
                  "The surrogate predicted logged PAR2 for unseen configurations from a withheld random-search "
                  "seed on the same training programs. The comparison used no final test data.", ""]
        for model in ("ridge_one_hot", "random_forest_one_hot"):
            if data["surrogate"]:
                score = statistics.mean(row[model]["spearman"] for row in data["surrogate"])
                regret = statistics.mean(row[model]["top_10_regret"] for row in data["surrogate"])
                lines.append(f"- {model} mean Spearman {score:.3f}, mean best-of-top-ten PAR2 regret {regret:.3f} s.")
        lines.append("")
        for row in data["candidate_generators"]:
            baseline = row["smac"]
            warm = row["sol-smac"]
            lines.append(f"- Seed {row['seed']} best initial training PAR2 relative to each run’s default · "
                         f"Sobol {baseline['best_ratio_to_default']:.3f}, Sol {warm['best_ratio_to_default']:.3f}. "
                         f"The initial batches took {baseline['batch_finished_s']:.1f} s and {warm['batch_finished_s']:.1f} s.")
    verifier = run / "VERIFY.json"
    if verifier.is_file():
        lines += ["", "`VERIFY.json` cross-checks the lock time, test-seed commitment, test hashes, "
                  "expected replay counts and per-session PAR2 fields against raw measurements."]
    temp = dst.with_suffix(".enriched")
    temp.write_text(body + "\n" + "\n".join(lines) + "\n")
    temp.replace(dst)


if __name__ == "__main__":
    main()
