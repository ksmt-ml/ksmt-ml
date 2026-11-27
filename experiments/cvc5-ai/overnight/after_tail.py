"""Continue the measured campaign after the recovered closed loop and concurrency check."""
import time

from pipeline import run_one
from replay import ROOT, STOP, load, log, now
from report import write_json

FIXED=[("14-returned-cores",["validate_cores.py"],"15-cores.json",
        "Проверяю возвращённые ядра предположений в независимом контексте Z3.",2200)]

def main():
    prior=ROOT/"protocols/tail-finished.json"
    while not prior.exists() and time.time()<STOP-600:
        time.sleep(5)
    if not prior.exists():
        return
    outcome={}
    for phase in FIXED:
        if STOP-time.time()>820:
            outcome[phase[0]]=run_one(*phase)
    write_json(ROOT/"protocols/after-tail-finished.json",{"at":now(),"results":outcome})
    log("Returned-core check done. Ready for the next bounded discriminating experiment.")
if __name__=="__main__":
    main()
