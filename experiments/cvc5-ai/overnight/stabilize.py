"""Recover historical validation finalists and run the prespecified repeat matrix."""
import sys

from replay import CAMPAIGN, DATA, ROOT, digest, ev, launch, load, verify_frozen
from report import build, write_json

RUN = ROOT / "runs/01-stability"

def recover():
    paths = [
        CAMPAIGN / "runs/campaign-v1/full/locks.json",
        ROOT.parent / "interactive/runs/interactive-v1/locks.json",
        CAMPAIGN / "runs/campaign-v1/smoke/locks.json",
    ]
    configs, lookup = {"default":{}}, {(): "default"}
    origins = {"default":[]}
    for path in paths:
        for run in load(path)["runs"]:
            for row in run["validation"]:
                params = ev.config(row["config"])
                key = tuple(params.items())
                if key not in lookup:
                    lookup[key] = f"C{len(lookup):02d}"
                    configs[lookup[key]] = params
                    origins[lookup[key]] = []
                arm = lookup[key]
                origins[arm].append({"lock":str(path.relative_to(ROOT.parent)),
                                     "seed":run["seed"],"arm":run["arm"],
                                     "candidate":row["candidate_id"],
                                     "historical_par2":row["par2"],
                                     "historical_counts":row["counts"],
                                     "winner":params == run["config"]})
    configs["default_alias"] = {}
    return configs, origins, paths

def main():
    frozen = verify_frozen()
    configs, origins, paths = recover()
    write_json(ROOT / "protocols/stability.json", {
        "locks":{str(p.relative_to(ROOT.parent)):digest(p) for p in paths},
        "verified_frozen_files":frozen, "configs":configs, "origins":origins,
        "repeats":12, "design":"randomized arms within each group × repeat, paired on same core; alias excluded from ranking",
        "analysis":"cluster paired bootstrap by program; repeated subsets and split-half; bootstrap same 11 programs separately from timing variability",
    })
    build(f"Начаты 12 парных повторов по 11 исходным программам. Восстановлено {len(configs)-1} разных векторов опций и добавлена повторная метка default.")
    launch(RUN,DATA / "validation/manifest.json",configs,12,422091,2900)
    import stability_metrics
    stability_metrics.analyze(RUN)
    build()

if __name__ == "__main__":
    main()
