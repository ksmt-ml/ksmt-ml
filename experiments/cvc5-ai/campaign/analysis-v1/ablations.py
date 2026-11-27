"""Use measured training logs for offline ablations without changing any lock."""
import argparse
import json
from pathlib import Path
import random
import statistics

from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge


def rows(p):
    return [json.loads(line) for line in p.read_text().splitlines()]


def candidates(root, seed, arm):
    file = root / "full" / f"seed-{seed}" / arm / "candidates.jsonl"
    return rows(file) if file.is_file() else []


def k(config):
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def offline(root, seeds, options):
    sample = {seed: [r for r in candidates(root, seed, "random") if r["eligible"] and r["config"]]
              for seed in seeds}
    records = []
    rng = random.Random(24699)
    features = [(name, value) for name, values in sorted(options.items()) for value in values]

    def x(rows):
        return [[int(r["config"].get(name, "auto") == value) for name, value in features] for r in rows]

    for seed in seeds:
        training = [r for other in seeds if other != seed for r in sample[other]]
        prior = {k(r["config"]) for r in training}
        held = [r for r in sample[seed] if k(r["config"]) not in prior]
        if len(held) < 10 or len(training) < 10:
            continue
        true = [r["par2"] for r in held]
        baseline = min(true)
        regressors = {
            "ridge_one_hot": Ridge(alpha=1.0),
            "random_forest_one_hot": RandomForestRegressor(n_estimators=300, min_samples_leaf=3,
                                                          max_features=0.7, n_jobs=1, random_state=19914),
        }
        out = {"split_seed": seed, "fit_candidates": len(training), "unseen_candidate_count": len(held),
               "oracle_best_training_par2": baseline, "constant_mae": statistics.mean(
                   abs(y - statistics.median(r["par2"] for r in training)) for y in true),
               "random_10_regret": statistics.mean(min(rng.sample(true, 10)) - baseline for _ in range(500))}
        for name, model in regressors.items():
            model.fit(x(training), [r["par2"] for r in training])
            pred = model.predict(x(held))
            order = sorted(range(len(true)), key=lambda i: (pred[i], i))
            out[name] = {"mae": statistics.mean(abs(float(p) - y) for p, y in zip(pred, true)),
                         "spearman": float(spearmanr(pred, true).statistic),
                         "top_1_regret": true[order[0]] - baseline,
                         "top_10_regret": min(true[i] for i in order[:10]) - baseline}
        records.append(out)
    return records


def warm_generators(root, seeds):
    pairs = []
    for seed in seeds:
        stats = {}
        for arm in ("smac", "sol-smac"):
            all_rows = candidates(root, seed, arm)
            initial = [r for r in sorted(all_rows, key=lambda q: q["candidate_id"]) if r["config"]][:4]
            default = next((r for r in all_rows if not r["config"] and r["eligible"]), None)
            admissible = [r for r in initial if r["eligible"]]
            if len(initial) == 4 and default is not None and admissible:
                best = min(r["par2"] for r in admissible)
                stats[arm] = {"default_train_par2": default["par2"], "initial_eligible": len(admissible),
                              "best_initial_train_par2": best, "best_ratio_to_default": best / default["par2"],
                              "batch_finished_s": initial[-1]["finished_s"],
                              "initial_candidate_ids": [x["candidate_id"] for x in initial]}
        if len(stats) == 2:
            pairs.append({"seed": seed, **stats})
    return pairs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    a = p.parse_args()
    lock = json.loads((a.run / "full/locks.json").read_text())
    seeds = sorted({r["seed"] for r in lock["runs"]})
    base = Path(__file__).resolve().parents[1]
    options = json.loads((base / "protocol-v1/options.json").read_text())
    output = {"scope": "training sessions only; neither offline ablation influenced selection",
              "surrogate": offline(a.run, seeds, options), "candidate_generators": warm_generators(a.run, seeds)}
    with (a.run / "ABLATIONS.json").open("x") as f:
        f.write(json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
