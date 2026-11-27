"""Measure remaining exact option vectors from earlier Interactive Sol smoke runs."""
from collections import Counter

import numpy as np

from replay import DATA, ROOT, digest, ev, launch, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, ratios, replay_cube

BASE=ROOT/"runs/01-stability-interactive-smoke"
SCOPE=ROOT/"protocols/stability-interactive-smoke.json"
REPEATS=12

def plan():
    if SCOPE.exists():
        return load(SCOPE)
    orig=load(ROOT/"protocols/stability.json")["configs"]
    scores=load(ROOT/"runs/01-stability/metrics.json")
    finalist=scores["best"]
    index={tuple(p.items()):k for k,p in orig.items() if k!="default_alias"}
    chosen={"default":{},"default_alias":{}}
    chosen[finalist]=orig[finalist]
    origin={}
    old=ROOT.parent/"interactive/runs"
    for directory in sorted(old.glob("smoke-*")):
        src=directory/"locked.json"
        if not src.exists():
            continue
        run=load(src)
        for entry in run["validation"]:
            cfg=ev.config(entry["config"])
            k=tuple(cfg.items())
            if k not in index:
                index[k]=f"IS{len(index)-len(orig)+2:02}"
                chosen[index[k]]=cfg
            a=index[k]
            origin.setdefault(a,[]).append({"smoke":directory.name,"arm":run["arm"],"seed":run["seed"],"lock_sha256":digest(src)})
    data={"locked":now(),"reason":"Three unique option vectors from Interactive Sol smoke were outside the full-lane recovered main matrix.",
          "configs":chosen,"origins":origin,"comparison":"rerun canonical baseline, alias and the main winner to control session/time differences",
          "original_matrix_winner":finalist,"used_for_any_tuning":False}
    write_json(SCOPE,data)
    return data

def report(check):
    scope=load(SCOPE)
    plan,groups,arms,cube,status,_=replay_cube(BASE)
    scores=cube.mean(axis=2)
    base=arms.index("default")
    winner=arms.index(scope["original_matrix_winner"])
    rng=np.random.default_rng(321893)
    draws=rng.integers(len(groups),size=(B,len(groups)))
    stats={}
    for i,arm in enumerate(arms):
        stats[arm]={"par2":float(scores[:,i].mean()),"ratio_default":ratios(scores[:,i],scores[:,base],draws),
                    "ratio_stability_winner":ratios(scores[:,i],scores[:,winner],draws),"counts":check["counts"][arm]}
    write_json(BASE/"metrics.json",{"programs":len(groups),"repeats":REPEATS,"results":stats})
    write_json(ROOT/"evidence/01zz-interactive-smoke.json",{
        "title":"01в · Дополнение шорт-листа Interactive Sol",
        "done":"Переоценены оставшиеся три уникальные настройки из интерактивных проб.",
        "summary":"Состав шорт-листа сверён с интерактивными пробными запусками. Добавлено парное сравнение трёх ранее непроверенных векторов.",
        "paragraphs":[f"Три точных вектора из smoke-01/02 отсутствовали в основной матрице. Для них на тех же {len(groups)} исходных программах запущены {REPEATS} повтора вместе с default, его дублем и лидером основной матрицы {scope['original_matrix_winner']}. Всего {check['rows']} запусков. Этот поздний контроль оставлен как отдельная серия, меню закрытых тестов уже зафиксировано.",
                      "Интервалы охватывают пересэмплирование исходных программ. Сравнение с лидером использует его повтор в этой же новой серии."],
        "tables":[card_table(f"Пробный шорт-лист · по {len(groups)*REPEATS} запусков на строку",
              ["Вектор","PAR2, с","К default [95% ДИ]","К лидеру [95% ДИ]","Таймауты","Ошибки + unknown + несовпадения"],
              [[a,f'{s["par2"]:.3f}',fmt_ci(s["ratio_default"]),fmt_ci(s["ratio_stability_winner"]),
                s["counts"].get("timeout",0),sum(s["counts"].get(k,0) for k in ("error","incorrect","unknown"))] for a,s in stats.items()])],
        "paths":["protocols/stability-interactive-smoke.json","runs/01-stability-interactive-smoke/plan.json",
                 "runs/01-stability-interactive-smoke/worker-{0..5}.jsonl","runs/01-stability-interactive-smoke/VERIFY.json",
                 "runs/01-stability-interactive-smoke/metrics.json"],
        "recommendation":"Сравнивать сокращения до нескольких флагов вместе с текущим default и влиянием таймаутов.",
        "next":"Проверить устойчивость отбора на отдельных семействах под рабочим лимитом."
    })
    build()

def main():
    s=plan()
    if not (BASE/"VERIFY.json").exists():
        check=launch(BASE,DATA/"validation/manifest.json",s["configs"],REPEATS,12209,900)
    else:
        check=load(BASE/"VERIFY.json")
    report(check)
if __name__=="__main__":
    main()
