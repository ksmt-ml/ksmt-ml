"""A short complete-stream comparator if the last mechanism probe finishes early."""
import time

from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json


def main():
    gate=ROOT/"protocols/model-request-tail-finished.json"
    while not gate.exists() and time.time()<STOP-12:
        time.sleep(3)
    row={"at":now(),"started":False}
    if gate.exists() and time.time()<STOP-330:
        row["started"]=True
        row["completed"]=run_one("33-portable-z3",["portable_solver_control.py"],"33-portable-z3.json",
            "Сверяю полный поток модели и ядер на установленных CLI Z3 и cvc5.",295)
    write_json(ROOT/"protocols/portable-solver-tail-finished.json",row)
    log("Portable full-stream Z3 control concluded or clock guard reached.")


if __name__=="__main__":
    main()
