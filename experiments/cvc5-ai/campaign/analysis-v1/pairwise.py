"""Compare the full held-out arm pairs on common programs and search seeds."""
import argparse
from collections import defaultdict
import itertools
import json
from pathlib import Path
import random
import statistics


ARMS = ("default", "random", "smac", "sol-smac", "sol-auto")
BOOT_SEED = 260918
BOOT_DRAWS = 5000


def ci(xs):
    q = statistics.quantiles(xs, n=40, method="inclusive")
    return [q[0], q[-1]]


def run(folder):
    par2 = defaultdict(list)
    eligible = defaultdict(lambda: True)
    for session_log in sorted(folder.glob("seed-*/sessions.jsonl")):
        for line in session_log.read_text().splitlines():
            row = json.loads(line)
            arm, seed, group = row["arm"], row["paired_seed"], row["group"]
            par2[arm, seed, group].append(row["par2"])
            if row["status"] in ("incorrect", "error", "budget"):
                eligible[arm, seed] = False
    means = {key: statistics.mean(values) for key, values in par2.items()}
    rng = random.Random(BOOT_SEED)
    answer = []
    for reference, arm in itertools.combinations(ARMS, 2):
        a = {(seed, group) for name, seed, group in means if name == arm and eligible[arm, seed]}
        b = {(seed, group) for name, seed, group in means if name == reference and eligible[reference, seed]}
        common = a & b
        seeds = sorted({seed for seed, _ in common})
        if not seeds:
            continue
        groups = sorted(set.intersection(*({group for s, group in common if s == seed} for seed in seeds)))
        cells = {s: {g: (means[arm, s, g], means[reference, s, g]) for g in groups} for s in seeds}

        def ratio(draw_seeds, draw_groups):
            chosen = [cells[s][g] for s in draw_seeds for g in draw_groups]
            return sum(a for a, _ in chosen) / sum(b for _, b in chosen)

        program_resampling = []
        crossed_resampling = []
        for _ in range(BOOT_DRAWS):
            draw_groups = rng.choices(groups, k=len(groups))
            program_resampling.append(ratio(seeds, draw_groups))
            crossed_resampling.append(ratio(rng.choices(seeds, k=len(seeds)), draw_groups))
        answer.append({"arm": arm, "reference": reference, "n_programs": len(groups),
                       "seeds": seeds, "ratio": ratio(seeds, groups),
                       "ratio_by_seed": {str(seed): ratio([seed], groups) for seed in seeds},
                       "program_ci_95": ci(program_resampling), "program_seed_ci_95": ci(crossed_resampling)})
    data = {"pairs": answer, "bootstrap_draws": BOOT_DRAWS, "bootstrap_seed": BOOT_SEED}
    with (folder / "pairwise.json").open("x") as file:
        file.write(json.dumps(data, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparison", type=Path)
    run(parser.parse_args().comparison)
