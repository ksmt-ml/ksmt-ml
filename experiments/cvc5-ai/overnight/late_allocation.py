"""Run the equal-count validation allocation test after the preceding correctness audit."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json

def main():
    gate=ROOT/"protocols/after-tail-finished.json"
    while not gate.exists() and time.time()<STOP-3100:
        time.sleep(5)
    state={"at":now(),"available_s":int(STOP-time.time()),"started":False}
    if gate.exists() and time.time()<STOP-3100:
        state["started"]=True
        state["complete"]=run_one("15-allocation",["allocation_probe.py"],"16-allocation.json",
                                  "Измеряю выбор большего числа семейств или большего числа повторов при одном числе запусков.",3200)
    write_json(ROOT/"protocols/last-scheduled-finished.json",state)
    log("Validation budget allocation stage concluded.")
if __name__=="__main__":
    main()
