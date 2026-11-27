"""Check whether the observed portable-library effect depends on one project."""
from collections import Counter

import numpy as np

from portfolio import MENU
from replay import ROOT, load
from report import build, write_json
from stability_metrics import card_table, replay_cube

BASE=ROOT/"runs/12-new-libraries"

def main():
    measured=BASE/"replay"
    plan,groups,arms,cube,st,wall=replay_cube(measured)
    verified=load(measured/"VERIFY.json")
    global_arm=load(MENU)["global_arm"]
    control=arms.index("default")
    compare=arms.index(global_arm)
    n,_,r=cube.shape
    project=[g.split(":",1)[0] for g in groups]
    ref=cube[:,control,:]
    tuned=cube[:,compare,:]
    labels=sorted(set(project))
    matrix=[]
    for lib in labels:
        mask=np.array([k==lib for k in project])
        statuses0=Counter(st[mask,control,:].reshape(-1))
        statuses1=Counter(st[mask,compare,:].reshape(-1))
        both=(st[mask,control]=="solved")&(st[mask,compare]=="solved")
        changes=(wall[mask,compare]-wall[mask,control])[both]
        e={"project":lib,"programs":int(mask.sum()),"default_par2":float(ref[mask].mean()),
           "global_par2":float(tuned[mask].mean()),"ratio_within":float(tuned[mask].mean()/ref[mask].mean()),
           "ratio_without_project":float(tuned[~mask].mean()/ref[~mask].mean()),
           "default_timeouts":statuses0["timeout"],"global_timeouts":statuses1["timeout"],
           "joint_solved_pair_count":int(changes.size),
           "median_matched_solved_delta_ms":float(1000*np.median(changes)) if changes.size else None}
        matrix.append(e)
    pair_default=st[:,control,:]
    pair_tuned=st[:,compare,:]
    switched={"default_timeout_global_solved":int(np.sum((pair_default=="timeout")&(pair_tuned=="solved"))),
              "default_solved_global_timeout":int(np.sum((pair_default=="solved")&(pair_tuned=="timeout")))}
    overall={"programs":n,"projects":len(labels),"repeats":r,"physical_rows":verified["rows"],
             "timeouts_switched":switched,"projects_analysis":matrix}
    write_json(measured/"sensitivity.json",overall)
    drop=[row["ratio_without_project"] for row in matrix]
    text=f"После исключения по одному проекту отношение C05/default менялось от {min(drop):.3f} до {max(drop):.3f}."
    write_json(ROOT/"evidence/13z-library-sensitivity.json",{
        "title":"13б · Чувствительность выигрыша к составу проектов",
        "done":"Проверены устойчивость результата к каждому проекту и смены исходов в парных прогонах.",
        "summary":text,
        "paragraphs":[
            f"Дополнительный анализ тех же {n} новых библиотечных методов из {len(labels)} проектов. Все {r} повторов оставлены внутри метода. Кластерная структура и настройки исходной проверки сохранены.",
            f"Из {n*r} пар C05 решил {switched['default_timeout_global_solved']} таймаутов default и дал {switched['default_solved_global_timeout']} новых таймаутов. Время среди решённых обоими режимами условно и описывает только такие пары.",
            "Удаление целого проекта сохраняет возможность увидеть вклад разных нагрузок. Оно не создаёт новые независимые проекты и само по себе не является подтверждающим тестом."
        ],
        "tables":[card_table("Четыре проекта · "+str(r)+" замеров каждого метода и режима",
                  ["Проект","Программ","Default, с","C05, с","C05/default внутри","C05/default без проекта",
                   "Таймауты default","Таймауты C05","Полных пар","Медиана Δ на полных парах, мс"],
                  [[x['project'],x['programs'],f"{x['default_par2']:.3f}",f"{x['global_par2']:.3f}",
                    f"{x['ratio_within']:.3f}",f"{x['ratio_without_project']:.3f}",x["default_timeouts"],x["global_timeouts"],
                    x["joint_solved_pair_count"],f"{x['median_matched_solved_delta_ms']:+.1f}" if x["median_matched_solved_delta_ms"] is not None else "—"]
                   for x in matrix])],
        "paths":["runs/12-new-libraries/replay/sensitivity.json","runs/12-new-libraries/replay/VERIFY.json",
                 "runs/12-new-libraries/portable-corpus/manifest.json","runs/12-new-libraries/portable-corpus/z3-audit.jsonl"],
        "recommendation":"Проверить C05 на очередных независимых проектах с теми же правилами языка и лимитом.",
        "next":"Сопоставить C05 с флагами теории массивов и общим default на ещё не использованных проектах."
    })
    build(current=load(ROOT/"current.json")["current"])
if __name__=="__main__":
    main()
