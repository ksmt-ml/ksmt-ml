import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import random
import statistics

from evaluator import SPACE, TUNING_TIMEOUT, aggregate, atomic_json, json_line, measure, verify


def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--core", type=int, required=True)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--timeout", type=float, default=TUNING_TIMEOUT)
    args = p.parse_args()
    os.sched_setaffinity(0, {args.core})
    items = verify(args.manifest)
    index = {item["group"]: item for item in items}
    args.output.mkdir(exist_ok=False, parents=True)
    rng = random.Random(19423)
    runs = defaultdict(list)
    for repeat in range(args.repeats):
        rng.shuffle(items)
        for item in items:
            row = measure(args.manifest, item, {}, args.timeout)
            runs[item["group"]].append(row)
            json_line(args.output / "sessions.jsonl", {**row, "repeat": repeat})
    by_type = defaultdict(list)
    for group, rows in runs.items():
        item = index[group]
        by_type[item["family"], item["size"]].extend(rows)
    summaries = []
    for (family, size), rows in sorted(by_type.items()):
        solved = [row["wall_s"] for row in rows if row["status"] == "solved"]
        summaries.append({"family": family, "size": size, **aggregate(rows),
                          "solved_median_s": statistics.median(solved) if solved else None})
    noise = []
    for group, rows in sorted(runs.items()):
        solved = [row["wall_s"] for row in rows if row["status"] == "solved"]
        noise.append({"group": group, "statuses": [r["status"] for r in rows],
                      "wall_s": [r["wall_s"] for r in rows],
                      "relative_range_solved": (max(solved) - min(solved)) / statistics.median(solved)
                      if len(solved) == args.repeats and len(solved) > 1 else None})
    # Validate single changes and sample interactions on the shortest solved group.
    quick = min((group for group, rows in runs.items() if all(r["status"] == "solved" for r in rows)),
                key=lambda group: statistics.median(r["wall_s"] for r in runs[group]))
    options = [{name: value} for name, values in SPACE.items() for value in values if value != "auto"]
    options += [{name: rng.choice(values) for name, values in SPACE.items()} for _ in range(32)]
    for settings in options:
        row = measure(args.manifest, index[quick], settings, args.timeout)
        json_line(args.output / "option-probes.jsonl", {"config": settings, **row})
    out = {"timeout_s": args.timeout, "core": args.core, "repeats": args.repeats, "seed": 19423,
           "by_level": summaries, "noise": noise}
    atomic_json(args.output / "summary.json", out)
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
