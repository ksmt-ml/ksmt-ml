"""Conditionally run the concurrency-aligned confirmation and protect the clock."""
import time

from pipeline import run_one
from replay import ROOT, STOP, load, log, now
from report import write_json

def main():
    gate=ROOT/"protocols/progress-tail-finished.json"
    while not gate.exists() and time.time()<STOP-3350:
        time.sleep(5)
    ready=ROOT/"runs/14b-search-retiming/metrics.json"
    report={"at":now(),"started":False,"retiming_available":ready.exists()}
    if gate.exists() and ready.exists() and time.time()<STOP-3350:
        report["retiming_gate"]=load(ready)["confirmatory_gate_passed"]
        if report["retiming_gate"]:
            report["started"]=True
            report["completed"]=run_one("20-aligned-search",["aligned_search.py"],"21-aligned-search.json",
                "Повторяю замкнутый поиск с той же шестипроцессной нагрузкой во время общей разминки.",3900)
    write_json(ROOT/"protocols/aligned-tail-finished.json",report)
    log("Aligned-search tail gate concluded.")
if __name__=="__main__":
    main()
