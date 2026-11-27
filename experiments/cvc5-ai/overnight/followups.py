"""Execute discriminating follow-ups as soon as the preceding measurements complete."""
import time

from pipeline import run_one
from replay import ROOT, STOP, load, log

STAGES=(
    ("01-interactive-smoke-supplement",["supplement.py"],"01zz-interactive-smoke.json",
     "Сверяю оставшиеся три точных вектора из пробных запусков Interactive Sol.",1000),
    ("07-solver-seeds",["solver_seed.py"],"08-solver-seeds.json",
     "Измеряю устойчивость оракула и глобальной настройки к внутренним seed cvc5.",4200),
    ("08-prefix",["prefix_probe.py"],"09-prefix.json",
     "Сравниваю Java-признаки с доступным до первого check префиксом SMT.",3200),
    ("09-restart",["restart_probe.py"],"10-restart.json",
     "Проверяю бюджетный портфель с реальным перезапуском целого сеанса.",2800),
    ("10-representation",["representation.py"],"11-representation.json",
     "Проверяю те же настройки на парных Java-программах с ветвлением и индексным массивом.",4400),
    ("11-profile",["profile_probe.py"],"12-profile.json",
     "Разделяю время проверок и построения моделей с парным контролем инструментирования.",3700),
)

def main():
    gate=ROOT/"protocols/phase-status.json"
    until=min(STOP-60,time.time()+23200)
    while not (gate.exists() and "06-cap-calibration" in load(gate)):
        if time.time()>until:
            raise TimeoutError("primary pipeline calibration did not complete within the allowed window")
        time.sleep(5)
    for stage in STAGES:
        if STOP-time.time()<300:
            log("Clock guard protects verification and final report")
            break
        run_one(*stage)
    log("Discriminating follow-up series finished")
    path=ROOT/"protocols/followups-finished.json"
    from report import write_json
    from replay import now
    write_json(path,{"ended":now(),"stage_names":[s[0] for s in STAGES]})

if __name__=="__main__":
    main()
