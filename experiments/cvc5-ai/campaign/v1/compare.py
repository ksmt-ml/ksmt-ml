"""Randomized paired held-out reruns, with group and search-seed uncertainty."""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import random
import statistics

from evaluator import TUNING_TIMEOUT, aggregate, atomic_json, json_line, measure, verify
from tune import ARMS


BOOTSTRAP_SEED = 0x59A2D19
ORDER_SEED = 0x226621


def rerun(test, locked, output, paired_seed, core, repeats, timeout):
    os.sched_setaffinity(0, {core})
    items = verify(test)
    rng = random.Random(ORDER_SEED ^ paired_seed)
    configs = {"default": {}}
    for arm in ARMS:
        row = locked.get(arm)
        if row and row["status"] == "ok" and row["config"] is not None:
            configs[arm] = row["config"]
    output.mkdir(exist_ok=False, parents=True)
    atomic_json(output / "plan.json", {"paired_seed": paired_seed, "core": core,
                "repeats": repeats, "timeout_s": timeout, "order_seed": ORDER_SEED ^ paired_seed,
                "configs": configs, "test_manifest": str(test)})
    rows = defaultdict(list)
    for repeat in range(repeats):
        rng.shuffle(items)
        for item in items:
            order = list(configs)
            rng.shuffle(order)
            for position, arm in enumerate(order):
                row = measure(test, item, configs[arm], timeout)
                rows[arm].append(row)
                json_line(output / "sessions.jsonl", {"arm": arm, "repeat": repeat,
                          "position": position, "paired_seed": paired_seed, **row})
    atomic_json(output / "summary.json", {
        arm: {"config": configs[arm], **aggregate(data)} for arm, data in rows.items()})


def interval(values):
    quantiles = statistics.quantiles(values, n=40, method="inclusive")
    return [quantiles[0], quantiles[-1]]


def report(comparisons, iterations=5000):
    rng = random.Random(BOOTSTRAP_SEED)
    by_cell = defaultdict(list)
    by_arm = defaultdict(list)
    paired = defaultdict(set)
    for round_dir in sorted(comparisons.glob("seed-*")):
        if not (round_dir / "summary.json").exists():
            continue
        for line in (round_dir / "sessions.jsonl").read_text().splitlines():
            row = json.loads(line)
            arm, seed, group = row["arm"], row["paired_seed"], row["group"]
            by_cell[arm, seed, group].append(row)
            by_arm[arm].append(row)
            paired[arm].add(seed)
    arms = {}
    for arm, all_rows in by_arm.items():
        seeds = sorted(paired[arm])
        groups_per_seed = {seed: {g for a, s, g in by_cell if a == arm and s == seed} for seed in seeds}
        common_groups = sorted(set.intersection(*groups_per_seed.values()))
        scores = {seed: {} for seed in seeds}
        for seed in seeds:
            for group in common_groups:
                scores[seed][group] = tuple(
                    statistics.mean(r["par2"] for r in by_cell[name, seed, group])
                    for name in (arm, "default"))
        per_seed = []
        for seed in seeds:
            cells = list(scores[seed].values())
            score = statistics.mean(a for a, _ in cells)
            reference = statistics.mean(d for _, d in cells)
            per_seed.append({"seed": seed, "par2": score, "default_par2": reference,
                             "delta_s": score - reference, "ratio": score / reference})
        arm_mean = statistics.mean(x["par2"] for x in per_seed)
        ref_mean = statistics.mean(x["default_par2"] for x in per_seed)

        def bootstrap(resample_seeds):
            deltas, ratios = [], []
            for _ in range(iterations):
                drawn_seeds = rng.choices(seeds, k=len(seeds)) if resample_seeds else seeds
                drawn_groups = rng.choices(common_groups, k=len(common_groups))
                cells = [scores[s][g] for s in drawn_seeds for g in drawn_groups]
                ours = sum(a for a, _ in cells)
                baseline = sum(d for _, d in cells)
                deltas.append((ours - baseline) / len(cells))
                ratios.append(ours / baseline)
            return {"delta_s_95": interval(deltas), "ratio_95": interval(ratios)}

        arms[arm] = {**aggregate(all_rows), "paired_seed_count": len(seeds), "group_count": len(common_groups),
                     "group_weighted_par2": arm_mean, "paired_default_par2": ref_mean,
                     "delta_s": arm_mean - ref_mean, "ratio": arm_mean / ref_mean, "per_seed": per_seed,
                     "group_bootstrap": bootstrap(False), "group_and_seed_bootstrap": bootstrap(True),
                     "by_group": {group: statistics.mean(scores[s][group][0] - scores[s][group][1]
                                                         for s in seeds) for group in common_groups}}
    summary = {"format": 1, "bootstrap_seed": BOOTSTRAP_SEED, "iterations": iterations,
               "score": "group-weighted mean session PAR2; repeats averaged within each seed and program",
               "arms": arms, "missing_arms": sorted(set(ARMS) - set(arms))}
    atomic_json(comparisons / "comparison.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("rerun")
    run.add_argument("test", type=Path)
    run.add_argument("locked", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--seed", required=True, type=int)
    run.add_argument("--core", required=True, type=int)
    run.add_argument("--repeats", type=int, default=3)
    run.add_argument("--timeout", type=float, default=TUNING_TIMEOUT)
    summarize = sub.add_parser("report")
    summarize.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.action == "rerun":
        rerun(args.test, json.loads(args.locked.read_text()), args.output, args.seed, args.core,
              args.repeats, args.timeout)
    else:
        report(args.output)


if __name__ == "__main__":
    main()
