"""Insert the longer-envelope native measurement after the independent profile."""
import time

from pipeline import run_one
from replay import ROOT, STOP, load, log, now
from report import write_json


def main():
    before=ROOT/"protocols/rewrite-profile-tail-finished.json"
    while not before.exists() and time.time()<STOP-12:
        time.sleep(4)
    state={"at":now(),"started":False}
    base=ROOT/"runs/25-native-rewrite/VERIFY.json"
    if before.exists() and base.exists() and time.time()<STOP-1200:
        r=load(base)
        if min(x["missing"]/r["planned_per_arm"] for x in r["counters"].values())>.25:
            state["started"]=True
            state["completed"]=run_one("25b-native-long-jvm",["native_budget_repair.py"],"25b-native-long-jvm.json",
                 "Повторяю живое сравнение с более длинным пределом JVM и прежними внутренними лимитами.",1140)
    write_json(ROOT/"protocols/native-budget-tail-finished.json",state)
    log("Native extended JVM comparison concluded or clock guard reached.")


if __name__=="__main__":
    main()
