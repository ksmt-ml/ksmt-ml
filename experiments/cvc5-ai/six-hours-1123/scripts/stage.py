"""Run one acquired stage under a workspace lock and update the atomic report."""
from datetime import datetime
from pathlib import Path
import fcntl
import json
import os
import shutil
import subprocess
import sys

from status import ROOT,atomic,now,status

PYTHON=ROOT.parent/".venv/bin/python"
CAMPAIGN_PYTHON=ROOT.parent/"campaign/.venv/bin/python"
CUTOFF="2026-11-23T09:21:00+03:00"
STAGES={
    "fp-encoding":("fp-encoding","Проверяется экспериментальное обратное FP кодирование",
             ["fp-encoding/src/run.py"],"fp-encoding/evidence/summary.json"),
    "cpu-dose":("cpu-dose","Измеряется доза CPU на совпадающей живой работе",
             ["cpu-dose/src/run.py"],"cpu-dose/evidence/summary.json"),
    "trace":("trace-cause","Измеряется, где расходятся траектории USVM",["trace-cause/src/run.py"],
             "trace-cause/evidence/summary.json"),
    "order-trace":("order-trace","Проверяется порядок утверждений на границе USVM и cvc5",
             ["order-trace/src/run.py"],"order-trace/evidence/summary.json"),
    "ast-shadow":("ast-shadow","Одинаковые объекты KSMT решаются в нескольких конфигурациях",
             ["ast-shadow/src/run.py"],"ast-shadow/evidence/summary.json"),
    "kernels":("kernels","Проверяются новые Java задачи с известными исходами",["kernels/src/run.py"],
             "kernels/evidence/summary.json"),
    "array-semantics":("array-semantics","Проверяется обработка STORE_ALL и достижимость исходов Java",
             ["array-semantics/src/run.py"],"array-semantics/evidence/summary.json"),
    "interactions":("interactions","Измеряется перенос эффективных BV переключателей",["interactions/src/run.py","interactions/src/analyze.py"],
             "interactions/evidence/summary.json"),
    "width":("width-study","Измеряется зависимость BV эффектов от разрядности и таймаута",
             ["width-study/src/run.py"],"width-study/evidence/summary.json"),
    "budget":("budget","Калибруются и проверяются ограничения native ресурсов",["budget/src/run.py"],
             "budget/evidence/summary.json"),
    "pure-resource":("pure-resource","Отделяется ресурсный предел от аварийного tlimit",
             ["pure-resource/src/run.py"],"pure-resource/evidence/summary.json"),
    "logic-study":("logic-study","Сравниваются узкая и широкая логики на тех же формулах",
             ["logic-study/src/run.py"],"logic-study/evidence/summary.json"),
    "logic-complement":("logic-complement","Проверяется перенос объявления логики на остальные формулы",
             ["logic-complement/src/run.py"],"logic-complement/evidence/summary.json"),
    "logic-unused-uf":("logic-unused-uf","Проверяется объявление неиспользуемых UF",
             ["logic-unused-uf/src/run.py"],"logic-unused-uf/evidence/summary.json"),
    "live-resource":("live-resource","Ресурсный лимит проверяется на живых библиотечных запусках",
             ["live-resource/src/run.py"],"live-resource/evidence/summary.json"),
    "resource-calibration":("live-resource","Калибруется предел ресурса на живых запросах",
             ["live-resource/src/run.py --calibration-only"],"live-resource/evidence/calibration-summary.json"),
    "resource-live-mini":("resource-live-mini","Короткая серия сравнивает ресурсный предел на живых запросах",
             ["resource-live-mini/src/run.py"],"resource-live-mini/evidence/summary.json"),
    "resource-live-hi":("resource-live-hi","Проверяется перенос более высокого ресурсного предела",
             ["resource-live-hi/src/run.py"],"resource-live-hi/evidence/summary.json"),
    "trace-overhead":("trace-overhead","Проверяется цена самой трассировки на одних и тех же методах",
             ["trace-overhead/src/run.py"],"trace-overhead/evidence/summary.json"),
    "closing-calibration":("closing-calibration","Проводится конечный контроль частоты и загрузки хоста",
             ["closing-calibration/src/run.py"],"closing-calibration/evidence/summary.json"),
    "panel":("library-panel","Идёт независимая библиотечная панель A/A, дозы и одной опции",
             ["library-panel/src/run.py","library-panel/src/precision.py"],"library-panel/evidence/summary.json"),
    "cold-search":("search","Запущен условный живой адаптивный поиск",
             ["search/src/live.py --cold"],"search/cold/evidence/live-summary.json"),
    "native-search":("native-search","Запущен условный поиск на нативных потоках",
             ["native-search/src/run.py --from-interactions"],"native-search/evidence/summary.json"),
    "array-search":("array-tuning","Сравниваются Sol, SMAC и random на новых задачах с массивами",
             ["array-tuning/src/run.py","array-tuning/src/analyze.py"],"array-tuning/evidence/summary.json"),
    "array-transfer":("array-transfer","Проверяется перенос массива STORE_ALL на другие библиотеки",
             ["array-transfer/src/run.py"],"array-transfer/evidence/summary.json"),
    "prompt-study":("prompt-study","Сравниваются две формы передачи фактических default для Sol",
             ["prompt-study/src/run.py"],"prompt-study/evidence/summary.json"),
    "smac-recovery":("smac-recovery","Восстанавливаются незапущенные оценки третьего SMAC",
             ["smac-recovery/src/run.py"],"smac-recovery/evidence/summary.json"),
}

def host_state():
    stat=Path("/proc/stat").read_text().splitlines()
    return dict(at=now(),boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
          load=Path("/proc/loadavg").read_text().strip(),free_bytes=shutil.disk_usage(ROOT).free,
          per_cpu_ticks={name.split()[0]:name.split()[1:] for name in stat
                         if name.split()[0] in {"cpu12","cpu13","cpu14","cpu15"}})

def run(name):
    folder,human,steps,seal=STAGES[name]
    path=ROOT/seal
    key={"array-semantics":"contrasts","kernels":"paired_ratios"}.get(name)
    if path.is_file() and (key is None or key in json.loads(path.read_text())):
        print("verified artifact exists",seal,now(),flush=True)
        return 0
    active=ROOT/"evidence/acquisition.lock"
    with active.open("a+") as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        print("stage",name,now(),flush=True)
        assert datetime.fromisoformat(now())<datetime.fromisoformat(CUTOFF)
        status("start-"+name,human,None,["Сверить результат после завершения пакетов",
                                         "Выбрать следующую проверку по результату"])
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1",TZ="Europe/Moscow",OMP_NUM_THREADS="1",
                 OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1",NUMEXPR_NUM_THREADS="1")
        interpreter=CAMPAIGN_PYTHON if name.endswith("search") or name=="smac-recovery" else PYTHON
        out=ROOT/folder/"evidence"
        out.mkdir(parents=True,exist_ok=True)
        launch=out/f"stage-{name}-{now()[11:19].replace(':','')}.log"
        metadata=launch.with_suffix(".host.json")
        host=dict(stage=name,start=host_state())
        atomic(metadata,json.dumps(host,indent=2)+"\n")
        with launch.open("x") as log:
            for stage in steps:
                cmd=["taskset","-c","12-15",str(interpreter),*stage.split()]
                print("subprocess",now()," ".join(cmd),file=log,flush=True)
                rc=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT).returncode
                print("returncode",rc,now(),file=log,flush=True)
                if rc:
                    host.update(end=host_state(),returncode=rc)
                    atomic(metadata,json.dumps(host,indent=2)+"\n")
                    status("failure-"+name,human+" завершилось технической ошибкой",None,
                           ["Проверить журнал "+str(launch.relative_to(ROOT)),"Возобновить только неполные этапы"])
                    subprocess.run([str(PYTHON),"scripts/render.py"],env=env,cwd=ROOT)
                    return rc
        host.update(end=host_state(),returncode=0)
        atomic(metadata,json.dumps(host,indent=2)+"\n")
        if (ROOT/seal).is_file():
            status("finish-"+name,"Данные этапа «"+human+"» записаны и проверяются",human+" — получены измерения",
                   ["Проверить условия следующего опыта"])
        else:
            status("stop-"+name,"Условный этап завершился по критерию допуска",None,["Проверить решение из evidence"])
        result=subprocess.run([str(PYTHON),"scripts/render.py"],env=env,cwd=ROOT).returncode
        print("stage returned",name,result,now(),flush=True)
        return result

if __name__=="__main__":
    sys.exit(run(sys.argv[1]))
