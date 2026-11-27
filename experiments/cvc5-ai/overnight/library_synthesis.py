"""Aggregate two nonoverlapping compatible library cohorts at the fixed 1.5s limit."""
from collections import Counter

import numpy as np

from portfolio import frac, group_draws
from replay import ROOT, digest, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci, replay_cube

SOURCES=(
    ("Повтор прежних проектов", ROOT/"runs/22-library-caps/replay", "default@1.5","C05@1.5"),
    ("Другие проекты", ROOT/"runs/23-rewrite-transfer/replay", "default","C05"),
)
OUT=ROOT/"runs/library-c05-synthesis.json"


def main():
    references=[]
    bases={}
    projects={}
    caps=[]
    options=[]
    for label,path,default,tuned in SOURCES:
        plan,groups,arms,par2,status,_=replay_cube(path)
        manifest=load(ROOT/plan["manifest"])
        sources={r["group"]:r for r in manifest["sessions"]}
        refs=[]
        for i,group in enumerate(groups):
            item=sources[group]
            j,k=arms.index(default),arms.index(tuned)
            refs.append({"group":group,"cohort":label,"project":item["family"],"input_sha256":item["sha256"],
                         "default_par2_s":float(par2[i,j,:].mean()),"c05_par2_s":float(par2[i,k,:].mean()),
                         "default_status":dict(Counter(status[i,j,:])),"c05_status":dict(Counter(status[i,k,:]))})
        grouped={r["project"] for r in refs}
        assert not grouped.intersection(projects)
        projects.update({p:label for p in grouped})
        assert all(plan["extra_flags_by_arm"][a]==["--arrays-exp"] for a in (default,tuned))
        caps.extend(plan["timeout_by_arm"].get(a,plan["timeout_s"]) for a in (default,tuned))
        options.append((plan["configs"][default],plan["configs"][tuned]))
        references.extend(refs)
        bases[label]={"programs":len(groups),"repeats":par2.shape[2],"projects":len(grouped),
                      "attempts_per_arm":len(groups)*par2.shape[2],"sha256":digest(path/"VERIFY.json")}
    assert options[0]==options[1] and caps==[1.5]*4
    assert len({q["input_sha256"] for q in references})==len(references)
    n=len(references)
    X=np.array([[r["default_par2_s"],r["c05_par2_s"]] for r in references])
    fam=[r["project"] for r in references]
    draw=group_draws(fam,np.random.default_rng(88261))
    score=frac(X[:,1],X[:,0],draw)
    off=X[:,1]-X[:,0]
    directions={"better":int(np.sum(off<0)),"worse":int(np.sum(off>0)),"ties":int(np.sum(off==0))}
    states={}
    for a in ("default","c05"):
        total=Counter()
        for r in references:
            total.update(r[a+"_status"])
        states[a]=total
    leave={}
    for project in sorted(projects):
        remaining=np.array([f!=project for f in fam])
        leave[project]=float(X[remaining,1].mean()/X[remaining,0].mean())
    result={"at":now(),"programs":n,"projects":len(projects),"ratio":score,
            "par2_default_s":float(X[:,0].mean()),"par2_C05_s":float(X[:,1].mean()),
            "program_directions":directions,"attempts_per_arm":sum(q["attempts_per_arm"] for q in bases.values()),
            "statuses":states,"sources":bases,"leave_one_project_out":leave,
            "program_effects":references,"settings":options[0],"cap_s":1.5,
            "approach":"Cross-study mechanism summary after the separate validation. Programs equally weighted after averaging repeats, whole-project bootstrap. Exact input digest and project sets disjoint."}
    write_json(OUT,result)
    base,tuned=states["default"],states["c05"]
    printline=f"В сумме двух непересекающихся групп из {n} библиотечных методов C05/default={fmt_ci(score)} при 1,5 с."
    write_json(ROOT/"evidence/24b-library-synthesis.json",{
        "title":"24б · Проверка повторяемости C05 по 14 библиотечным проектам",
        "summary":printline,"done":"Сведены два непересекающихся библиотечных набора с одинаковыми флагами и лимитом.",
        "paragraphs":[
            f"Сведены две уже измеренные серии, {n} различных полных записей, {len(projects)} проектов. Сверены точные хеши, разные проекты, одинаковые option-векторы и общий --arrays-exp. Повторы усреднены внутри метода, 95% ДИ пересэмплирует целые проекты.",
            f"По {result['attempts_per_arm']} фактических запусков на режим. У default {base['timeout']} таймаутов, у C05 {tuned['timeout']}. Ошибки {base['error']} и {tuned['error']}, неправильные ответы {base['incorrect']} и {tuned['incorrect']}. При исключении любого проекта отношение {min(leave.values()):.3f}–{max(leave.values()):.3f}.",
            f"У {directions['better']} методов средний PAR2 уменьшился, у {directions['worse']} вырос, у {directions['ties']} совпал. Это сводный расчёт после отдельных опытов. Он сохраняет их границы и даёт каждому методу одинаковый вес.",
        ],
        "tables":[card_table("Два источника · разные проекты и точные SMT-LIB хеши",
                    ["Серия","Методов","Проектов","Повторов","Сеансов на режим"],
                    [[label,q["programs"],q["projects"],q["repeats"],q["attempts_per_arm"]] for label,q in bases.items()])],
        "paths":[str(OUT.relative_to(ROOT)),"runs/22-library-caps/replay/VERIFY.json",
                 "runs/23-rewrite-transfer/replay/VERIFY.json"],
        "recommendation":"Проверять настройку на целевом живом клиенте с тем же типом потока, лимитом и версией решателя.",
        "next":"Повторить сравнение на живом cvc5 1.4.1 с парным контролем и полным учётом исходов.",
    })
    build(load(ROOT/"current.json")["current"])


if __name__=="__main__":
    main()
