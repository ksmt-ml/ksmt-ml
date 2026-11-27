"""Run a bounded startup-cost test after any aligned search."""
import time
from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json

def main():
    gate=ROOT/"protocols/aligned-tail-finished.json"
    while not gate.exists() and time.time()<STOP-12:
        time.sleep(4)
    state={"at":now(),"started":False}
    if gate.exists() and time.time()<=STOP-950:
        state["started"]=True
        state["completed"]=run_one("21-reset-batches",["reset_batches.py"],"22-reset-batches.json",
             "Измеряю цену запуска cvc5 при пакетах коротких полных библиотечных сеансов.",1000)
    write_json(ROOT/"protocols/reset-tail-finished.json",state)
    log("Persistent-process probe concluded or clock guard reached.")
if __name__=="__main__":
    main()
