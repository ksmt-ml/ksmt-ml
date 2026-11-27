"""Run the objective diagnostic only when the last scheduled measurement releases its cores."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json

def main():
    before=ROOT/"protocols/core-tail-finished.json"
    while not before.exists() and time.time()<STOP-1600:
        time.sleep(5)
    state={"at":now(),"started":False}
    if before.exists() and time.time()<STOP-1600 and (ROOT/"protocols/progress-objective.json").exists():
        state["started"]=True
        state["complete"]=run_one("19-progress-objective",["progress_probe.py"],"20-progress-objective.json",
           "Проверяю отбор по частичным ответам в уже измеренном архиве обучения.",2200)
    write_json(ROOT/"protocols/progress-tail-finished.json",state)
    log("Partial-progress comparison complete or clock guard reached.")
if __name__=="__main__":
    main()
