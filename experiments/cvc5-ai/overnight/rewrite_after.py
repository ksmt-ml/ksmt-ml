"""Launch the independently scoped transfer check after any cap measurements."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json


def main():
    gate = ROOT / "protocols/caps-tail-finished.json"
    while not gate.exists() and time.time() < STOP - 12:
        time.sleep(4)
    marker = {"at": now(), "started": False}
    if gate.exists() and time.time() < STOP - 1500:
        marker["started"] = True
        marker["completed"] = run_one(
            "23-rewrite-transfer", ["rewrite_transfer.py"], "24-rewrite-transfer.json",
            "Проверяю перенос фиксированных флагов переписывания на ранее не использованных проектах.", 1450,
        )
    write_json(ROOT / "protocols/rewrite-tail-finished.json", marker)
    log("Rewrite transfer confirmation concluded or clock guard reached.")


if __name__ == "__main__":
    main()
