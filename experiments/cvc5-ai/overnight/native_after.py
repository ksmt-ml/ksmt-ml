"""Run a live native timing confirmation after the portable settings validation."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json


def main():
    gate = ROOT / "protocols/rewrite-tail-finished.json"
    while not gate.exists() and time.time() < STOP - 12:
        time.sleep(4)
    state = {"at": now(), "started": False}
    compatibility = ROOT / "runs/24-native-options/api-exceptions-fixed/VERIFY.json"
    if gate.exists() and compatibility.exists() and time.time() < STOP - 1100:
        state["started"] = True
        state["completed"] = run_one(
            "25-native-rewrite", ["native_variant.py"], "25-native-rewrite.json",
            "Проверяю флаги переписывания в живом USVM на нативном cvc5 1.1.2.", 1050,
        )
    write_json(ROOT / "protocols/native-rewrite-tail-finished.json", state)
    log("Native option time measurement concluded or clock guard reached.")


if __name__ == "__main__":
    main()
