"""Run the array-family follow-up only once the decoder has an audited working encoding."""
import time
from pipeline import run_one
from replay import ROOT, STOP, load, log, now
from report import write_json

def main():
    dependency=ROOT/"protocols/live-tail-finished.json"
    while not dependency.exists() and time.time()<STOP-2250:
        time.sleep(5)
    study=ROOT/"runs/11b-array-bridge/replay/metrics.json"
    report={"at":now(),"started":False}
    if dependency.exists() and study.exists() and load(study)["next_encoding"] and time.time()<STOP-2250:
        report["started"]=True
        report["complete"]=run_one("17-arrays-options",["arrays_options.py"],"18-array-options.json",
             "Проверяю два параметра теории массивов на других проектах после зафиксированного преобразования.",3400)
    write_json(ROOT/"protocols/after-live-finished.json",report)
    log("Array-theory follow-up concluded or clock guard reached.")
if __name__=="__main__":
    main()
