"""Advance from the completed stability matrix through the precommitted portfolio."""
import time

from replay import ROOT, STOP, digest, load, log, now
from report import build, write_json
from portfolio import run

def main():
    stability=ROOT/"evidence/01-stability.json"
    until=min(STOP,time.time()+3150)
    while not stability.exists():
        if time.time()>until:
            raise TimeoutError("stability evidence unavailable after bounded wait")
        time.sleep(3)
    dev=run("dev")
    scope=ROOT/"protocols/route-before-holdout.json"
    if not scope.exists():
        write_json(scope,{"locked":now(),"stability_sha256":digest(stability),
                         "development_sha256":digest(ROOT/"runs/02-portfolio/dev-replay/metrics.json"),
                         "development_oracle_headroom":dev["disjoint_oracle_headroom"],
                         "decision":"Test frozen portfolio and new real-Java full-session transfer; run matched Sol/RF/native SMAC closed loop with real feedback and independent new holdout if Sol passes isolated smoke.",
                         "reason":"Disjoint-repeat oracle estimates room for an achievable selector. Real library sessions distinguish gain tied to the generator from workload transfer. Three previous offline model pools cannot identify whether any ranking advantage survives live feedback and CLI wall overhead.",
                         "holdout_rules":"Do not select finalist, thresholds, or conclusion from new test measurements. Real transfer libraries and search holdout are separate; search receives the original train bundle only.",
                         "mass_measurements_stop":"2026-11-22T10:48:00+03:00"})
        scope.chmod(0o444)
    build("Селектор и меню зафиксированы. Записываю полные сеансы закрытых семейств портфеля.")
    run("test")

if __name__=="__main__":
    main()
