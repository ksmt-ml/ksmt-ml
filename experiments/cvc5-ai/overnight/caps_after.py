"""Reserve a paired library-cap diagnostic after the already staged work."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json


def main():
    gate = ROOT / "protocols/reset-tail-finished.json"
    while not gate.exists() and time.time() < STOP - 12:
        time.sleep(4)
    state = {"at": now(), "started": False}
    if gate.exists() and time.time() < STOP - 1320:
        state["started"] = True
        state["completed"] = run_one(
            "22-library-caps", ["library_caps.py"], "23-library-caps.json",
            "Измеряю устойчивость C05 к трём лимитам полного библиотечного сеанса.", 1250,
        )
    write_json(ROOT / "protocols/caps-tail-finished.json", state)
    log("Library session cap comparison concluded or clock guard reached.")


if __name__ == "__main__":
    main()
