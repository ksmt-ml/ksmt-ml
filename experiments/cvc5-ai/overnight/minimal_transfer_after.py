"""Use any remaining substantial runway for independent option confirmation."""
import time

from pipeline import run_one
from replay import ROOT, STOP, load, log, now
from report import write_json


def main():
    dependent=ROOT/"protocols/simplification-tail-finished.json"
    while not dependent.exists() and time.time()<STOP-12:
        time.sleep(4)
    state={"at":now(),"started":False}
    verification=ROOT/"runs/29-simplification/replay/metrics.json"
    if dependent.exists() and verification.exists() and time.time()<STOP-470:
        candidate=load(verification)["effects"]["no_simpl"]
        state["development_ratios"]={"to_full":candidate["to_full"],"to_default":candidate["to_default"]}
        if candidate["to_full"][0]<=1.05 and candidate["to_default"][0]<=.97:
            fresh=time.time()<STOP-790
            state["started"]=True
            state["fresh"]=fresh
            args=["minimal_transfer.py"] if fresh else ["minimal_transfer.py","--reuse"]
            text=("Подтверждаю один флаг переписывания на ещё одной группе независимых библиотек." if fresh else
                  "Проверяю однофлаговый вариант на восьми ранее записанных библиотечных проектах.")
            state["completed"]=run_one("31-minimal-transfer",args,"31-minimal-transfer.json",text,745 if fresh else 445)
    write_json(ROOT/"protocols/minimal-transfer-tail-finished.json",state)
    log("Independent minimal setting confirmation concluded or clock guard reached.")


if __name__=="__main__":
    main()
