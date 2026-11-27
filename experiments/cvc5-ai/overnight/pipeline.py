"""Run successive completed probes, record failures, and keep continuing within the clock guard."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from replay import ROOT, PYTHON, STOP, check_budget, log, load, now
from report import build, write_json

STAGES=(
    ("04-transfer",["transfer.py"],"04-transfer.json","Записываю свежие упорядоченные сеансы USVM на локальных Java-библиотеках.",3600),
    ("05-sol-smoke",["search.py","smoke"],None,"Проверяю изоляцию Sol, реальные ответы модели, RF и native SMAC на обучении.",700),
    ("05-search",["search.py","full"],"06-closed-loop.json","Запускаю замкнутый поиск Sol, RF и native SMAC с одинаковым стартом и временем.",9200),
    ("06-cap-calibration",["calibration.py"],"07-calibration.json","Сравниваю взаимодействие опций при двух лимитах сеанса на отдельной калибровочной выборке.",5000),
)

def phase_result(name,code,elapsed,logpath,marker=None):
    dest=ROOT/"protocols/phase-status.json"
    ledger=load(dest) if dest.exists() else {}
    record={"ended":now(),"returncode":code,"wall_s":elapsed,"stdout_stderr":str(logpath.relative_to(ROOT)),
            "evidence":marker,"status":"completed" if code==0 else "failed"}
    ledger[name]=record
    write_json(dest,ledger)
    log(f'{record["status"].upper()} {name}: rc={code}, {elapsed:.1f}s')
    return record

def failure_note(name,rec):
    path=ROOT/"evidence/04z-operational.json"
    obj=load(path) if path.exists() else {
        "title":"Технические сбои и восстановление",
        "done":"Записаны фактические технические сбои и способы продолжения.",
        "summary":"Зафиксированы сбои выполнения. Сводки проверенных измерений доступны по этапам.",
        "paragraphs":[],"paths":[],"tables":[],
        "recommendation":"Учитывать статус допуска каждого эксперимента при интерпретации результатов.",
        "next":"Продолжить независимые эксперименты с завершёнными входными данными.",
    }
    obj["paragraphs"].append(f"Этап {name} завершился с кодом {rec['returncode']} за {rec['wall_s']:.1f} с. Подробности сохранены в журнале этапа.")
    obj["paths"].append(rec["stdout_stderr"])
    write_json(path,obj)
    build()

def run_one(name,args,marker,description,cap):
    check_budget()
    if marker and (ROOT/"evidence"/marker).is_file():
        return True
    supported=ROOT/"runs/05-search/smoke/VERIFY.json"
    if name=="05-search" and not (supported.exists() and load(supported)["ok"]):
        log("SKIP full online search: passed isolated smoke missing; advancing independent run")
        return False
    if name=="05-sol-smoke" and supported.exists() and load(supported)["ok"]:
        return True
    build(description)
    log(f"PIPELINE {name}")
    stage_log=ROOT/"runs"/(name+".out")
    if stage_log.exists():
        stage_log=ROOT/"runs"/(name+"-"+time.strftime("%H%M%S")+".out")
    start=time.monotonic()
    end=start+min(cap,STOP-time.time()-12)
    guard_failure=None
    env=dict(os.environ,OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1",MKL_NUM_THREADS="1",
             NUMEXPR_NUM_THREADS="1",VECLIB_MAXIMUM_THREADS="1",PYTHONDONTWRITEBYTECODE="1")
    with stage_log.open("wb") as out:
        p=subprocess.Popen([PYTHON,"-B",*args],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=out,
                           start_new_session=True)
        try:
            while p.poll() is None:
                check_budget()
                if time.monotonic()>=end:
                    break
                time.sleep(2)
        except (TimeoutError, RuntimeError, subprocess.SubprocessError) as exc:
            guard_failure=type(exc).__name__
        finally:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGKILL)
                    p.wait()
    rc=p.returncode
    if rc==0 and marker and not (ROOT/"evidence"/marker).exists():
        rc=92
    rec=phase_result(name,rc,time.monotonic()-start,stage_log,marker)
    if guard_failure:
        log(f"Measurement guard interrupted {name}: {guard_failure}")
    if rc:
        failure_note(name,rec)
    return rc==0

def main():
    expected=ROOT/"evidence/03-portfolio.json"
    limit=min(STOP-20,time.time()+4400)
    while not expected.exists():
        if time.time()>=limit:
            raise TimeoutError("Portfolio gate missing")
        time.sleep(4)
    for stage in STAGES:
        if STOP-time.time()<90:
            log("Hard guard stops additional phase launch")
            break
        run_one(*stage)
    log("Scheduled primary pipeline finished. Further probes selected from verified results.")

if __name__=="__main__":
    main()
