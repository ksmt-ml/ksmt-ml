"""Run the direct alignment diagnostic after the other six-core experiments."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json


def main():
    gate = ROOT / "protocols/native-budget-tail-finished.json"
    while not gate.exists() and time.time() < STOP - 12:
        time.sleep(4)
    result = {"at": now(), "started": False}
    if gate.exists() and time.time() < STOP - 1710:
        result["started"] = True
        result["completed"] = run_one(
            "28-warm-load", ["warm_load.py"], "28-warm-load.json",
            "Сравниваю два и шесть воркеров на фиксированных стартовом и позднем векторах.", 1660,
        )
    write_json(ROOT / "protocols/warm-load-tail-finished.json", result)
    log("Warm worker load diagnostic concluded or clock guard reached.")


if __name__ == "__main__":
    main()
