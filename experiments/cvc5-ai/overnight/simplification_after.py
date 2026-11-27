"""Measure the minimal rewriting vector if the remaining clock allows it."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json


def main():
    gate = ROOT / "protocols/warm-load-tail-finished.json"
    while not gate.exists() and time.time() < STOP - 12:
        time.sleep(4)
    receipt = {"at": now(), "started": False}
    if gate.exists() and time.time() < STOP - 800:
        receipt["started"] = True
        receipt["completed"] = run_one(
            "29-simplification", ["simplification_probe.py"], "29-simplification.json",
            "Отделяю simplification=none от остальных параметров переписывания в cvc5 1.4.1.", 730,
        )
    write_json(ROOT / "protocols/simplification-tail-finished.json", receipt)
    log("Rewriting option ablation concluded or clock guard reached.")


if __name__ == "__main__":
    main()
