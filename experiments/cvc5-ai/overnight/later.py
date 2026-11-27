"""Continue from verified follow-ups on fresh library families."""
import time

from pipeline import run_one
from replay import ROOT, STOP, load, log

def main():
    gate=ROOT/"protocols/followups-finished.json"
    until=STOP-300
    while not gate.exists() and time.time()<until:
        time.sleep(5)
    if gate.exists() and time.time()<until-240:
        run_one("12-new-libraries",["fresh_transfer.py"],"13-new-libraries.json",
                "Проверяю перенос правил на ранее неиспользованных Java-проектах.",4900)
    log("Independent library follow-up completed or reached the clock guard")
if __name__=="__main__":
    main()
