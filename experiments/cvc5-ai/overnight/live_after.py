"""Launch the empirical native-client follow-up once earlier workers release their cores."""
import time
from pipeline import run_one
from replay import ROOT, STOP, log, now
from report import write_json

def main():
    gate=ROOT/"protocols/last-scheduled-finished.json"
    while not gate.exists() and time.time()<STOP-2900:
        time.sleep(5)
    result={"at":now(),"started":False}
    if gate.exists() and time.time()<STOP-2900:
        result["started"]=True
        result["completed"]=run_one("16-live-client",["live_client.py"],"17-live-client.json",
             "Запускаю живой USVM на фиксированных методах с нативными Z3 и cvc5.",4900)
    write_json(ROOT/"protocols/live-tail-finished.json",result)
    log("Live-client experiment concluded or clock guard reached.")
if __name__=="__main__":
    main()
