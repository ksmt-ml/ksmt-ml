"""Attribute a library effect only if it reproduced to a useful magnitude."""
import time

from pipeline import run_one
from replay import ROOT, STOP, load, log, now
from report import write_json


def main():
    gate = ROOT / "protocols/witness-tail-finished.json"
    while not gate.exists() and time.time() < STOP - 12:
        time.sleep(4)
    state = {"at": now(), "started": False}
    analysis = ROOT / "runs/23-rewrite-transfer/replay/metrics.json"
    if gate.exists() and analysis.exists() and time.time() < STOP - 1100:
        ratio = load(analysis)["effects"]["rewrite"]["to_default"]
        state["confirmatory_ratio"] = ratio
        state["gate"] = "Run if ratio <= 0.97 and project-bootstrap upper bound <= 1.02"
        if ratio[0] <= 0.97 and ratio[1][1] <= 1.02:
            state["started"] = True
            state["completed"] = run_one(
                "27-rewrite-profile", ["rewrite_profile.py"], "27-rewrite-profile.json",
                "Измеряю вклад проверок, моделей и ядер в эффект флагов переписывания.", 1020,
            )
    write_json(ROOT / "protocols/rewrite-profile-tail-finished.json", state)
    log("Rewriting command profile concluded or clock guard reached.")


if __name__ == "__main__":
    main()
