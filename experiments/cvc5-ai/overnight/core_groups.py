"""Measure transfer of a fixed tuning choice across two physical CPU classes."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess

import numpy as np

from portfolio import MENU, frac, group_draws
from replay import DATA, ROOT, digest, ev, launch, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE=ROOT/"runs/18-core-classes"
SCOPE=ROOT/"protocols/core-classes.json"
CLASSES={"SMT_4p7GHz":(2,4,6),"single_3p4GHz":(8,10,12)}
R=9

def snapshot():
    mapping={}
    for name,cores in CLASSES.items():
        for core in cores:
            path=Path(f"/sys/devices/system/cpu/cpu{core}")
            mapping[core]={"class":name,
              "siblings":(path/"topology/thread_siblings_list").read_text().strip(),
              "base_governor":(path/"cpufreq/scaling_governor").read_text().strip(),
              "maximum_hardware_frequency_khz":int((path/"cpufreq/cpuinfo_max_freq").read_text()),
              "actual_khz":int((path/"cpufreq/scaling_cur_freq").read_text())}
    assert len({p["siblings"] for p in mapping.values()})==6
    return mapping

def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    original=DATA/"validation/manifest.json"
    new=ROOT/"runs/12-new-libraries/portable-corpus/manifest.json"
    bridge=load(ROOT/"protocols/new-libraries.json")["array_bridge"]
    assert bridge
    menu=load(MENU)
    params={"default":{},"default_alias":{},"global":menu["arms"][menu["global_arm"]]}
    bases=(("original Java",original),("portable libraries",new))
    common=[]
    BASE.mkdir(exist_ok=True)
    for label,manifest in bases:
        for item in load(manifest)["sessions"]:
            real=item["family"] if label=="portable libraries" else item["group"]
            common.append({**item,"family":real,"provenance":label,
                          "file":os.path.relpath(manifest.parent/item["file"],BASE)})
    out=BASE/"manifest.json"
    write_json(out,{"group_count":len(common),"sessions":common,"scope":"repeat same frozen workloads across CPU classes"})
    ev.verify(out)
    shape=snapshot()
    plan={
        "locked":now(),"why":"Configuration advantage can move at the wall cap when processor speed changes, even on identical files. Direct within-host comparison on reused inputs.",
        "core_classes":CLASSES,"hardware_snapshot":shape,"repeats":R,
        "groups":"3 physical cores with SMT siblings and max 4.7 GHz versus 3 physical cores without SMT siblings and max 3.4 GHz",
        "control":"Same recorded program list, options, common arrays-exp, cap 1.5 s and repeat/arm order in both CPU classes. Neither governors nor existing host activity were modified. No second campaign worker on SMT siblings.",
        "freshness":"Reused original validation and previously released portable library full sessions (mechanism only). Distinct program denominators by workload. Programs paired between CPU classes.",
        "arm_configuration":params,"manifest_sha256":digest(out),"source_hashes":{k:digest(v) for k,v in bases},
        "hypothesis":"whether C05/default PAR2 or timeout impact changes across the CPU groups",
    }
    write_json(SCOPE,plan)
    SCOPE.chmod(0o444)
    return plan

def measure(plan):
    manifest=BASE/"manifest.json"
    assert plan["manifest_sha256"]==digest(manifest)
    with ThreadPoolExecutor(max_workers=2) as worker:
        jobs=[]
        for name,cores in CLASSES.items():
            path=BASE/name
            if (path/"VERIFY.json").exists():
                continue
            extra={a:["--arrays-exp"] for a in plan["arm_configuration"]}
            jobs.append(worker.submit(launch,path,manifest,plan["arm_configuration"],R,127896,1800,
                                      cores=cores,extra_flags_by_arm=extra))
        for job in jobs:
            job.result()
    write_json(BASE/"hardware-after.json",snapshot())

def analyze(plan):
    stored=load(BASE/"manifest.json")
    actual={x["group"]:x for x in stored["sessions"]}
    cohorts=sorted({r["provenance"] for r in actual.values()})
    outputs={name:replay_cube(BASE/name) for name in CLASSES}
    assert all(o[1]==next(iter(outputs.values()))[1] for o in outputs.values())
    groups=next(iter(outputs.values()))[1]
    diff={}
    for lab in cohorts:
        users=np.array([i for i,g in enumerate(groups) if actual[g]["provenance"]==lab])
        keys=[actual[groups[i]]["family"] for i in users]
        sampled=group_draws(keys,np.random.default_rng(785862))
        weights=sampled.sum(axis=1)
        block={}
        scores={}
        for name,(_,_,arms,values,status,wall) in outputs.items():
            subset=values[users,:,:].mean(axis=2)
            modes={a:subset[:,arms.index(a)] for a in arms}
            c={a:Counter(status[users,arms.index(a),:].ravel()) for a in arms}
            block[name]={"programs":len(users),"repeats":values.shape[2],"scores":{
                a:{"par2":float(v.mean()),"ratio_to_default":frac(v,modes["default"],sampled),"counts":c[a]} for a,v in modes.items()}}
            scores[name]=modes
        high,low=(scores[x] for x in CLASSES)
        fitted=(sampled@low["global"])/(sampled@low["default"])
        fast=(sampled@high["global"])/(sampled@high["default"])
        relative=(low["global"].mean()/low["default"].mean())/(high["global"].mean()/high["default"].mean())
        base_gap=(sampled@(low["default"]-high["default"]))/weights
        diff[lab]={"data":block,"change_in_relative_gain":[float(relative),np.percentile(fitted/fast,[2.5,97.5]).tolist()],
                   "default_wall_score_gap_s":float((low["default"]-high["default"]).mean()),
                   "default_gap_interval":np.percentile(base_gap,[2.5,97.5]).tolist()}
    result={"programs":len(groups),"repeats_per_core_class":R,"cohorts":diff,
            "physical_rows":sum(load(BASE/n/"VERIFY.json")["rows"] for n in CLASSES)}
    write_json(BASE/"metrics.json",result)
    change=diff["portable libraries"]["change_in_relative_gain"]
    p=diff["portable libraries"]["data"]
    summary=(f"На тех же библиотечных сеансах отношение эффекта C05 на двух группах CPU "
             f"составило {fmt_ci(change)}. PAR2 default {p['SMT_4p7GHz']['scores']['default']['par2']:.3f} "
             f"и {p['single_3p4GHz']['scores']['default']['par2']:.3f} с.")
    write_json(ROOT/"evidence/19-core-classes.json",{
        "title":"19 · Перенос настройки между классами физических ядер",
        "done":"Проверена чувствительность C05 к физическим ядрам с разными частотным пределом и SMT.",
        "summary":summary,
        "paragraphs":[
            f"Повторно использованы {len(groups)} программы в двух отдельных нагрузках, {R} повторов на пару «настройка × группа CPU». Сравнили три физических ядра с SMT и пределом 4,7 ГГц и три без SMT с пределом 3,4 ГГц. Максимум шесть воркеров. Одинаковый случайный порядок в обеих группах, два режима идут одновременно.",
            "Сторонняя работа на сервере и частоты продолжают меняться. Классы различались по фактически доступным CPU, статистики до и после сохранены. Все измерения получают общий --arrays-exp, включая два контрольных default.",
            f"На библиотечных записях отношение (C05/default на 3,4 ГГц) / (C05/default на 4,7 ГГц): {fmt_ci(change)}. Интервал парно пересэмплирует библиотечные проекты. Для исходной Java-валидации кластеры — отдельные программы."
        ],
        "tables":[card_table(f"Сравнение CPU · {R} повторов каждого режима и группы",["Нагрузка","Группа CPU","Программ",
                     "Флаги","PAR2, с","К default [95% ДИ]","Таймауты","Ошибки + unknown + несовпадения"],
                    [[name,k,entry["programs"],a,f'{t["par2"]:.3f}',fmt_ci(t["ratio_to_default"]),
                      t["counts"].get("timeout",0),sum(t["counts"].get(q,0) for q in ("error","unknown","incorrect"))]
                     for name,d in diff.items() for k,entry in d["data"].items() for a,t in entry["scores"].items()])],
        "paths":["protocols/core-classes.json","runs/18-core-classes/manifest.json",
                 "runs/18-core-classes/<группа>/VERIFY.json",
                 "runs/18-core-classes/hardware-after.json","runs/18-core-classes/metrics.json"],
        "recommendation":"Для применения фиксировать класс ядер и оценивать долю таймаутов отдельно от времени уже решённых сеансов.",
        "next":"Повторить выбранную конфигурацию на той группе CPU и версии решателя, где будут реальные ограничения клиента."
    })
    build()

def main():
    s=freeze()
    measure(s)
    analyze(s)
if __name__=="__main__":
    main()
