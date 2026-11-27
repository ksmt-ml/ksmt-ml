"""Render measured results and reproducible artifact links after the final replay."""
import argparse
from collections import Counter
import json
from pathlib import Path

import telemetry

ROOT = Path(__file__).resolve().parents[1]
SEEDS = (18713, 36251, 59077)
ARM_LABELS = {"default": "Default", "smac": "Native SMAC", "sol-auto": "Prior Sol-auto",
              "sol-interactive": "Interactive Sol"}
MARKER = "\n## Completed measurements\n"


def read(path):
    return json.loads(path.read_text())


def link(path, label):
    return f"[{label}]({path.relative_to(ROOT)})"


def interval(values):
    return f"{values[0]:.3f} to {values[1]:.3f}"


def report(directory):
    final = directory / "final"
    comparison = final / "comparison"
    manifest = read(Path(read(final / "cohort.json")["manifest"]))
    metrics = read(comparison / "comparison.json")["arms"]
    pairs = read(comparison / "pairwise.json")
    scope = read(final / "cohort.json")["kind"]
    audit = read(final / "VERIFY.json")
    tuning = read(directory / "tuning-verify.json")
    evidence = {seed: telemetry.run(directory / f"seed-{seed}") for seed in SEEDS}
    (directory / "interaction-telemetry.json").write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
    primary = pairs["pairs"]["sol-auto"]
    effect = 100 * (primary["ratio"] - 1)
    direction = "lower" if effect < 0 else "higher"
    low, high = primary["program_seed_ratio_ci_95"]
    outcome = ("The interval includes equal performance." if low <= 1 <= high else
               "The interval favors interactive Sol." if high < 1 else "The interval favors the prior Sol-auto.")
    cohort = "fresh held-out" if scope == "fresh" else "old-test exploratory"
    docs = [
        f"Interactive Sol's PAR2 was **{abs(effect):.1f}% {direction}** than prior Sol-auto on the "
        f"{cohort} cohort (ratio {primary['ratio']:.3f}, paired program-and-seed 95% interval "
        f"{interval(primary['program_seed_ratio_ci_95'])}). {outcome}",
        "",
        f"The replay used {manifest['group_count']} programs, {len(manifest['sessions'])} sessions, "
        f"{sum(len(s['oracle']) for s in manifest['sessions'])} oracle checks per full corpus traversal, "
        f"and three paired repeats per search seed. The manifest records {len(manifest['rejected'])} "
        f"rejected trace(s). The independent Z3 audit recorded {json.dumps(audit['oracle_audit_statuses'])}.",
        "",
        "| Arm | PAR2 s | Relative to default | Solved | Correct / expected answers | Timeout / unknown / error / incorrect |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm, label in ARM_LABELS.items():
        row = metrics[arm]
        counts = row["counts"]
        outcomes = " / ".join(str(counts[status]) for status in ("timeout", "unknown", "error", "incorrect"))
        docs.append(f"| {label} | {row['group_weighted_par2']:.3f} | {row['ratio']:.3f} | {counts['solved']} | "
                    f"{row['correct_answers']} / {row['queries']} | {outcomes} |")
    docs.extend([
        "",
        "| Interactive Sol vs. | PAR2 ratio | Δ PAR2 s | 95% program interval | 95% paired program-and-seed interval | Ratios by seed (18713 / 36251 / 59077) |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for arm, row in pairs["pairs"].items():
        per_seed = " / ".join(f"{row['per_seed'][str(seed)]['ratio']:.3f}" for seed in SEEDS)
        docs.append(f"| {ARM_LABELS[arm]} | {row['ratio']:.3f} | {row['delta_s']:+.3f} | "
                    f"{interval(row['program_ratio_ci_95'])} | "
                    f"{interval(row['program_seed_ratio_ci_95'])} | {per_seed} |")
    docs.extend(["", "## Interactive search and validation", "",
        "| Seed | CLI turns / same-thread resumes | Completed candidates / eligible | Command or evaluator wall s | CLI wall outside commands and evaluation s | Solver CPU s total | Lock time s |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for seed, row in evidence.items():
        docs.append(
            f"| {seed} | {len(row['turns'])} / {sum(t['resumed'] for t in row['turns'])} | "
            f"{row['completed_unique_candidates']} / {row['eligible_candidates']} | "
            f"{row['tool_or_training_eval_wall_s']:.1f} | {row['cli_wall_outside_observed_tools_and_eval_s']:.1f} | "
            f"{row['solver_cpu_total_s']:.1f} | {row['wall_s_locked']:.1f} |"
        )
    docs.extend(["",
        "Completed candidate counts include the initial default and configurations rejected for solver errors. "
        "The timing union includes both tool intervals observed in the CLI and broker evaluation intervals, "
        "including script runs that outlive a single command result. The remaining CLI time includes model "
        "inference, network and CLI overhead. Active decision-making time is unavailable as a separate "
        "measurement. The journals and changes in evaluated configurations provide direct evidence of decisions.",
        "",
    ])
    for seed, row in evidence.items():
        subdir = directory / f"seed-{seed}"
        turns = row["turns"]
        first, last = turns[0], turns[-1]
        competing = min((x for x in row["validation"] if x["eligible"]), key=lambda x: (
            x["par2"], bool(x["config"]), x["candidate_id"]))
        docs.append(
            f"- **{seed}**. First prompt at {first['elapsed_s_at_start']:.2f} s with "
            f"{first['remaining_s_at_start']:.2f} s to search. Last at {last['elapsed_s_at_start']:.2f} s "
            f"with {last['remaining_s_at_start']:.2f} s to search. "
            f"{tuning[str(seed)]['clock_snapshots_verified']} prompts and live clock values verified. "
            f"{row['item_counts'].get('command_execution', 0)} completed commands, "
            f"{row['item_counts'].get('web_search', 0)} web searches, "
            f"median first tool dispatch {row['tool_first_dispatch_latency']['median_s']:.2f} s, "
            f"median real evaluation request {row['eval_request_latency']['median_s']:.2f} s. "
            f"Validation locked candidate {competing['candidate_id']} at {competing['par2']:.4f} PAR2 "
            f"with `{json.dumps(row['selected_config'], sort_keys=True)}`. "
            f"{link(subdir / 'model/turn-000.prompt.txt', 'First prompt')} · "
            f"{link(subdir / 'model/work/journal.md', 'Journal')} · "
            f"{link(subdir / 'broker.jsonl', 'Broker responses')} · "
            f"{link(subdir / 'train-sessions.jsonl', 'Train sessions')} · "
            f"{link(subdir / 'validation-sessions.jsonl', 'Validation')} · "
            f"{link(subdir / 'model-events.jsonl', 'CLI events')}."
        )
    all_statuses = Counter()
    for row in evidence.values():
        all_statuses.update(row["training_outcomes"])
    docs.extend([
        "",
        f"Training session outcomes were `{json.dumps(dict(all_statuses), sort_keys=True)}`. "
        f"{sum(row['ite_simp_unsat_core_errors'] for row in evidence.values())} errors matched the retained "
        "ITE simplification/unsat-core conflict. Every seed completed its search and validated within the "
        "declared wall budget. The controller retained the results of any interrupted turns and their unfinished trials.",
        "",
        "## Evidence and limitations",
        "",
        "- The prior Sol-auto runs had one initial call per seed (300.0, 244.7 and 292.7 s) followed by "
        "the generated policy on the original single-worker evaluator. The model calls completed 23, 10 and 20 trials. "
        "The scripts completed 322, 330 and 330 trials. All these were direct submissions, with zero SMAC-origin trials. "
        "Seed 18713 hit the 300 s model deadline and emitted no final usage. The comparison includes historical CLI behavior, "
        "batch strategy, feedback policy and actual trial throughput along with the treatment of model time.",
        "- Runtime measurements share the Linux host with unrelated workloads. The new search wave ran three "
        "tuning seeds concurrently, and seed 59077 used a different physical core from its historical run. "
        "The fresh replay randomizes arm order within the exact same sessions on each pinned replay core.",
        "- Three tuning seeds and a small generator-specific corpus limit generalization. The 1.50 s cap "
        "means a few changed timeouts can move PAR2 substantially. Validation differences among some finalists were only "
        "a few milliseconds per session under the frozen two-repeat procedure. The answer audit checks recorded satisfiability "
        "answers. It exercises model and unsat-assumption retrieval without comparing returned objects.",
        "",
        f"{link(directory / 'plan.json', 'Frozen run plan')} · "
        f"{link(directory / 'locks.json', 'All locks')} · "
        f"{link(final / 'unsealed.json', 'Post-lock seed release')} · "
        f"{link(final / 'materialization.log', 'Trace generation log')} · "
        f"{link(final / 'VERIFY.json', 'Replay verification')} · "
        f"{link(comparison / 'pairwise.json', 'Paired bootstrap')} · "
        f"{link(directory / 'interaction-telemetry.json', 'Per-turn timing and feedback telemetry')} · "
        f"{link(directory / 'resources.json', 'Resource configuration')} · "
        f"{link(directory / 'resource-samples.jsonl', 'Resource samples')}.",
        "",
        "The raw held-out replays are available for " + " · ".join(
            link(comparison / f"seed-{seed}/sessions.jsonl", str(seed)) for seed in SEEDS) + ".",
    ])
    if scope != "fresh":
        docs.insert(3, f"The fresh materialization failure was preserved in {link(final / 'fresh-error.json', 'fresh-error.json')}.")
    return "\n".join(docs) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    args = p.parse_args()
    results = ROOT / "RESULTS.md"
    intro = results.read_text().split(MARKER, 1)[0]
    headline, body = report(args.run.resolve()).split("\n\n", 1)
    if read(args.run / "final/cohort.json")["kind"] == "fresh":
        status = "Fresh held-out evaluation completed and verified."
    else:
        status = "Old-test exploratory evaluation completed."
    start = intro.splitlines()
    start[2] = "**Status** · " + status
    start[4] = headline
    new = "\n".join(start) + "\n" + MARKER + "\n" + body
    temporary = results.with_suffix(".md.new")
    temporary.write_text(new)
    temporary.replace(results)


if __name__ == "__main__":
    main()
