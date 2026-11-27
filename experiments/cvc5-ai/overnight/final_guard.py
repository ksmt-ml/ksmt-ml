"""Finalize verification if the live controller is between checks at the deadline."""
import time

from finish import run
from replay import ROOT, STOP, log

def main():
    while time.time()<STOP+35:
        time.sleep(min(20,STOP+35-time.time()))
    if (ROOT/"campaign-verification.json").exists():
        return
    run(final=True,full=True)
if __name__=="__main__":
    main()
