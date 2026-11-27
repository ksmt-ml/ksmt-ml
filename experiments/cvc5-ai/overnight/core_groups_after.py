"""Launch a different hardware mechanism probe before the hard clock guard."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json

def main():
    prior=ROOT/"protocols/after-live-finished.json"
    while not prior.exists() and time.time()<STOP-1750:
        time.sleep(5)
    result={"at":now(),"started":False}
    if prior.exists() and time.time()<STOP-1750 and (ROOT/"runs/12-new-libraries/portable-corpus/manifest.json").exists():
        result["started"]=True
        result["completed"]=run_one("18-core-classes",["core_groups.py"],"19-core-classes.json",
            "Парно сравниваю перенос C05 между двумя группами физических ядер.",2200)
    write_json(ROOT/"protocols/core-tail-finished.json",result)
    log("CPU class comparison concluded or clock guard reached.")
if __name__=="__main__":
    main()
