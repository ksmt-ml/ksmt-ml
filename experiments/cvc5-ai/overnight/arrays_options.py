"""Isolate two array theory settings on new project families with portable streams."""
from collections import Counter
from pathlib import Path

import numpy as np

from bridge import BASE as BRIDGE, independent_single
from portfolio import MENU, frac, group_draws
from replay import CORES, ROOT, digest, ev, launch, load, log, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube
from transfer import capture, locate

BASE=ROOT/"runs/17-arrays-options"
SCOPE=ROOT/"protocols/arrays-options.json"
LIBS=(("trove4j","trove4j-1.0.20200330.jar","gnu.trove.",20),
      ("jopt-simple","jopt-simple-4.9.jar","joptsimple.",18),
      ("org-json","json-20180813.jar","org.json.",16),
      ("byte-buddy","byte-buddy-1.12.20.jar","net.bytebuddy.description.type.",18),
      ("commons-logging","commons-logging-1.2.jar","org.apache.commons.logging.",13))
REPEATS=8
CAP=1.50
FLAGS_DATA={
    "default":["--arrays-exp"],
    "index":["--arrays-exp","--arrays-eager-index"],
    "no_linear":["--arrays-exp","--no-arrays-optimize-linear"],
    "both":["--arrays-exp","--arrays-eager-index","--no-arrays-optimize-linear"],
    "default_alias":["--arrays-exp"],
}

def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    m=BRIDGE/"replay/metrics.json"
    result=load(m)
    port=result["next_encoding"]
    assert port in ("tuple","nested")
    artifacts=[]
    for name,jar,package,count in LIBS:
        file=locate(jar)
        artifacts.append({"project":name,"jar":str(file),"sha256":digest(file),"package":package,
                          "max_classes":count,"method_ms":7500,"solver_ms":2500})
    menu=load(MENU)
    configs={n:{} for n in FLAGS_DATA}
    settings=menu["arms"][menu["global_arm"]]
    configs["global"]=settings
    preprocess={"ext-rew-prep","repeat-simp","simplification"}
    configs["global_bv"]={k:v for k,v in settings.items() if k not in preprocess}
    configs["global_rewrite"]={k:v for k,v in settings.items() if k in preprocess}
    assert configs["global_bv"] and configs["global_rewrite"]
    cfg_flags={**FLAGS_DATA,**{k:["--arrays-exp"] for k in ("global","global_bv","global_rewrite")}}
    from real_selector import prepare as prepare_selector, LOCK as SELECTOR
    prepare_selector()
    data={"locked":now(),"library_selector_lock_sha256":digest(SELECTOR),"question":"After resolving nonportable Z3 syntax, do eager index splitting and the linear array optimization interact with runtime and errors of whole real-ish JVM library methods?",
          "artifacts":artifacts,"configs":configs,"extra":cfg_flags,"minimum_admitted_total":16,
          "array_bridge":port,"bridge_metrics_sha256":digest(m),"repeats":REPEATS,
          "prior_scope":"Different project families. Admission and rewriting rely on completion, semantic audit and type shape, never test timing.",
          "conditions":"All new modes set arrays-exp. Compare the two named array switches as a 2x2 design, and separately split C05 into its bitvector and preprocessing option groups. Shared default and duplicate.",
          "followup_basis_sha256":digest(ROOT/"runs/12-new-libraries/replay/sensitivity.json"),
          "primary":"Estimated interaction on program-paired PAR2 and error counts, bootstrap whole project; show subset with actual multi-index conversion separately (descriptive)."}
    write_json(SCOPE,data)
    SCOPE.chmod(0o444)
    return data

def smoke(s):
    out=BASE/"smoke"
    if (out/"VERIFY.json").exists():
        assert load(out/"VERIFY.json")["ok"]
        return
    out.mkdir(parents=True)
    test_manifest=BRIDGE/s["array_bridge"]/"paired-manifest.json"
    candidates=sorted([r for r in load(test_manifest)["sessions"] if r["bridge_edited"]],
                      key=lambda r:len(r["oracle"]))[:2]
    assert len(candidates)==2
    preferred=ev.flags
    import os
    prev=os.sched_getaffinity(0)
    logs=Counter()
    records=[]
    try:
        os.sched_setaffinity(0,{CORES[0]})
        for rec in candidates:
            for label,parameters in s["configs"].items():
                flags=s["extra"][label]
                ev.flags=lambda p: [*preferred(p),*flags]
                obs=ev.measure(test_manifest,rec,parameters,CAP)
                logs[obs["status"]]+=1
                records.append({"arm":label,**obs})
    finally:
        ev.flags=preferred
        os.sched_setaffinity(0,prev)
    ok=not any(logs[k] for k in ("error","unknown","incorrect"))
    write_json(out/"VERIFY.json",{"ok":ok,"programs":2,"repeats":1,"counts":logs,"rows":records})
    assert ok

def analyze(out,manifest,scope):
    meta=load(manifest)
    plan,groups,arms,cube,st,wall=replay_cube(out)
    P,A,R=cube.shape
    v=load(out/"VERIFY.json")
    by_group={x["group"]:x for x in meta["sessions"]}
    family=[by_group[g]["family"] for g in groups]
    mult=group_draws(family,np.random.default_rng(286262))
    pars=cube.mean(axis=2)
    ref=pars[:,arms.index("default")]
    vals={k:pars[:,i] for i,k in enumerate(arms)}
    out_stats={k:{"par2":float(y.mean()),"ratio":frac(y,ref,mult),"counts":v["counts"][k]} for k,y in vals.items()}
    main=vals["both"]-vals["index"]-vals["no_linear"]+ref
    difference={"interaction_s":float(main.mean()),
                "ci":np.percentile((mult@main)/mult.sum(axis=1),[2.5,97.5]).tolist()}
    part=vals["global"]-vals["global_bv"]-vals["global_rewrite"]+ref
    c05_additivity={"value_s":float(part.mean()),
                    "ci":np.percentile((mult@part)/mult.sum(axis=1),[2.5,97.5]).tolist()}
    subtype=np.array([by_group[g]["bridge_edited"] for g in groups])
    if subtype.any() and not subtype.all():
        pick={k:{"par2_rewritten":float(y[subtype].mean()),"par2_unchanged":float(y[~subtype].mean())}
              for k,y in vals.items()}
    else:
        pick={}
    r={"programs":P,"projects":len(set(family)),"repeats":R,"rows":v["rows"],
       "subtype_rewritten":int(subtype.sum()),"adapter_excluded":meta["adapter_excluded"],
       "states":out_stats,"interaction":difference,"c05_option_groups_interaction":c05_additivity,"by_syntactic_type":pick,"failed_projects":meta["failed_projects"]}
    write_json(out/"metrics.json",r)
    n_projects=r["projects"]
    d=difference
    headline=f"На {P} новых библиотечных методах совместный эффект двух параметров массивов составил {1000*d['interaction_s']:+.1f} мс PAR2."
    write_json(ROOT/"evidence/18-array-options.json",{
        "title":"18 · Параметры теории массивов на других проектах",
        "done":f"Проверено {v['rows']} запусков с параметрами массивов на {n_projects} новых проектах.",
        "summary":headline,
        "paragraphs":[
            f"Новые полные записи для {P} Java-методов, один на класс. {n_projects} проектов и {R} повторов на ячейку. Во всех сравнениях общий --arrays-exp. Преобразование {scope['array_bridge']} после независимого Z3 применено к {int(subtype.sum())} методам.",
            f"Сравнение пары опций включает отдельно --arrays-eager-index и --no-arrays-optimize-linear, их комбинацию и контроль без них. Интервал взаимодействия {1000*d['ci'][0]:+.1f}…{1000*d['ci'][1]:+.1f} мс. Проблемы разбора, таймауты и несовпадения сохранены по флагам.",
            f"Отдельно C05 разложен на битвекторные параметры и параметры переписывания. Контраст C05 минус изолированные группы плюс default {1000*c05_additivity['value_s']:+.1f} мс, ДИ [{1000*c05_additivity['ci'][0]:+.1f}; {1000*c05_additivity['ci'][1]:+.1f}]. Группы опций зафиксированы по предыдущему библиотечному результату до создания этих записей.",
            "Параметры сравнивались в случайном порядке внутри одного и того же полного сеанса. Оценка относится к этой группе библиотек и общей кодировке. Интервалы пересэмплируют целые проекты."
        ],
        "tables":[card_table(f"Режимы · {P} программ, {R} повторов, {P*R} запусков на строку",
                    ["Параметры","PAR2, с","К общему default [95% ДИ]","Таймауты","Ошибки","Unknown","Несовпадения"],
                    [[k,f'{q["par2"]:.3f}',fmt_ci(q["ratio"]),*[q["counts"].get(x,0) for x in ("timeout","error","unknown","incorrect")]]
                     for k,q in out_stats.items()])],
        "paths":["protocols/arrays-options.json","runs/17-arrays-options/smoke/VERIFY.json",
                 "runs/17-arrays-options/portable/manifest.json","runs/17-arrays-options/replay/VERIFY.json",
                 "runs/17-arrays-options/replay/metrics.json"],
        "recommendation":"Менять параметры массивов только после проверки репрезентативной доли совместимых полных сеансов.",
        "next":"Подтвердить механизм на зафиксированном реальном клиентском следе с измерением времени модели и проверки."
    })
    build()

def main():
    s=freeze()
    smoke(s)
    base=BASE/"corpus"
    raw=base/"manifest.json"
    if not raw.exists():
        raw=capture(base,s,SCOPE)
    portable=BASE/"portable/manifest.json"
    if not portable.exists():
        portable=independent_single(raw,portable.parent,s["array_bridge"])
    assert load(portable)["group_count"]>=s["minimum_admitted_total"]
    d=BASE/"replay"
    if not (d/"VERIFY.json").exists():
        launch(d,portable,s["configs"],REPEATS,614092,2400,extra_flags_by_arm=s["extra"])
    analyze(d,portable,s)
    from real_selector import evaluate, LOCK as SELECTOR
    assert s["library_selector_lock_sha256"]==digest(SELECTOR)
    evaluate(d,portable)
if __name__=="__main__":
    main()
