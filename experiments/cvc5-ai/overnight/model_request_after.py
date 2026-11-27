"""Use the remaining safe measurement window for one targeted mechanism probe."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json


def main():
    gate=ROOT/"protocols/paired-rewrite-tail-finished.json"
    while not gate.exists() and time.time()<STOP-12:
        time.sleep(3)
    state={"at":now(),"started":False}
    if gate.exists() and time.time()<STOP-440:
        state["started"]=True
        state["completed"]=run_one("32-model-requests",["model_request_cost.py"],"32-model-requests.json",
            "Измеряю влияние запросов модели на эффект переписывания при одинаковых проверках.",415)
    write_json(ROOT/"protocols/model-request-tail-finished.json",state)
    log("Model request mechanism probe concluded or clock guard reached.")


if __name__=="__main__":
    main()
