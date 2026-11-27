"""Run recovered primary search and a measured host-control study after the queued probes."""
import time

from pipeline import run_one
from replay import ROOT, STOP, load, log, now
from report import write_json

PRIMARY=("05-search",["search.py","full"],"06-closed-loop.json",
         "Запускаю прошедшее пробу сравнение Sol, RF и native SMAC на равном времени.",9500)
HOST=("13-concurrency",["concurrency_probe.py"],"14-concurrency.json",
      "Калибрую влияние одного и шести воркеров в случайном парном порядке на исходной валидации.",2400)

def main():
    gate=ROOT/"protocols/phase-status.json"
    until=STOP-540
    while not (gate.exists() and "12-new-libraries" in load(gate)):
        if time.time()>=until:
            log("clock guard: no later measurement stage")
            return
        time.sleep(6)
    result={}
    if STOP-time.time()>=6400 and not (ROOT/"evidence/06-closed-loop.json").exists():
        result["closed_loop"]=run_one(*PRIMARY)
    if STOP-time.time()>=1250:
        result["concurrency"]=run_one(*HOST)
    write_json(ROOT/"protocols/tail-finished.json",{"at":now(),"phases":result})
    log("Completed planned tail, next test will follow the evidence.")
if __name__=="__main__":
    main()
