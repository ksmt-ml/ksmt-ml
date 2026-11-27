"""Run the independent matched-structure check while enough time remains."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json


def main():
    gate = ROOT / "protocols/minimal-transfer-tail-finished.json"
    while not gate.exists() and time.time() < STOP - 12:
        time.sleep(4)
    state = {"at": now(), "started": False}
    if gate.exists() and time.time() < STOP - 770:
        fresh = time.time() < STOP - 1235
        state["started"] = True
        state["fresh"] = fresh
        arguments = ["paired_rewrite.py"] if fresh else ["paired_rewrite.py","--reuse"]
        description = ("Проверяю перенос переписывания на новых парных формах ветвления и таблицы." if fresh else
                       "Проверяю флаги переписывания на ранее записанных парных формах ветвления и таблицы.")
        state["completed"] = run_one(
            "30-paired-rewrite", arguments, "30-paired-rewrite.json", description, 1220 if fresh else 720,
        )
    write_json(ROOT / "protocols/paired-rewrite-tail-finished.json", state)
    log("Fresh paired rewrite transfer concluded or clock guard reached.")


if __name__=="__main__":
    main()
