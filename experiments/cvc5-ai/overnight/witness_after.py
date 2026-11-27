"""Run the independent model-value audit after native timings are released."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json


def main():
    gate = ROOT / "protocols/native-rewrite-tail-finished.json"
    while not gate.exists() and time.time() < STOP - 12:
        time.sleep(4)
    result = {"at": now(), "started": False}
    if gate.exists() and time.time() < STOP - 900:
        result["started"] = True
        result["completed"] = run_one(
            "26-model-witnesses", ["model_witness.py"], "26-model-witnesses.json",
            "Сверяю выбранные значения моделей cvc5 с исходными ограничениями в Z3.", 840,
        )
    write_json(ROOT / "protocols/witness-tail-finished.json", result)
    log("Model witness audit concluded or clock guard reached.")


if __name__ == "__main__":
    main()
