"""Compare the very same frozen default inputs across campaign epochs."""
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
import json

import numpy as np

from replay import CAMPAIGN, DATA, ROOT, digest, lines, load
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, ratios

def historical_default(directory):
    cohorts=sorted(directory.glob("seed-*/**/validation-sessions.jsonl"))
    assert cohorts
    collected=[(path,r) for path in cohorts for r in lines(path) if r["candidate_id"]==0]
    return collected

def main():
    old=CAMPAIGN/"runs/campaign-v1/full"
    inter=ROOT.parent/"interactive/runs/interactive-v1"
    fresh=ROOT/"runs/01-stability"
    by=[historical_default(old),historical_default(inter),
        [(p,r) for p in sorted(fresh.glob("worker-*.jsonl")) for r in lines(p) if r["arm"]=="default"]]
    oracle={m["file"]:m for m in load(DATA/"validation/manifest.json")["sessions"]}
    groups=sorted(record["group"] for record in oracle.values())
    manifestsha=digest(DATA/"validation/manifest.json")
    rng=np.random.default_rng(8101)
    draw=rng.integers(len(groups),size=(B,len(groups)))
    measurements={}
    sets=["Первая кампания","Interactive Sol","Эта ночь"]
    for stage,items in zip(sets,by,strict=True):
        grouped=defaultdict(list)
        paths=set()
        for p,r in items:
            truth=oracle[r["session"]]
            assert truth["group"]==r["group"] and r["queries"]==len(truth["oracle"])
            assert r["par2"]==(r["wall_s"] if r["status"]=="solved" else 3.0)
            assert r["answers"]==[t for t in truth["oracle"]][:len(r["answers"])]
            grouped[r["group"]].append(r)
            paths.add(p)
        assert list(sorted(grouped))==groups
        repeats={len(x) for x in grouped.values()}
        assert len(repeats)==1
        G={}
        all_status=Counter()
        for g,rows in grouped.items():
            solved=[r for r in rows if r["status"]=="solved"]
            counts=Counter(r["status"] for r in rows)
            all_status.update(counts)
            G[g]={"counts":dict(counts),"par2":mean(r["par2"] for r in rows),
                  "median_solved_wall_s":median(r["wall_s"] for r in solved) if solved else None,
                  "median_solved_cpu_s":median(r["solver_cpu_s"] for r in solved) if solved else None,
                  "median_wall_cpu_gap_s":median(r["wall_s"]-r["solver_cpu_s"] for r in solved) if solved else None}
        solved_all=[r for _,r in items if r["status"]=="solved"]
        measurements[stage]={"replays":len(items),"repeats":next(iter(repeats)),"counts":all_status,"programs":G,
                             "par2":mean(data["par2"] for data in G.values()),
                             "median_wall_cpu_gap_ms":1000*median(r["wall_s"]-r["solver_cpu_s"] for r in solved_all),
                             "files":{str(p.relative_to(ROOT.parent)):digest(p) for p in sorted(paths)}}
    ref=np.array([measurements[sets[0]]["programs"][g]["par2"] for g in groups])
    result={s:{**m,"ratio_first":ratios(np.array([m["programs"][g]["par2"] for g in groups]),ref,draw)}
            for s,m in measurements.items()}
    stats={"manifest_sha256":manifestsha,"independent_programs":len(groups),"stage_summaries":result,
           "comparison":"descriptive same-program cross-epoch comparison; host load and run schedule were not randomized across epochs",
           "source_hashes":{str(path.relative_to(ROOT.parent)):digest(path) for path in
                [old/"locks.json",inter/"locks.json",fresh/"VERIFY.json"]}}
    write_json(fresh/"archival-default.json",stats)
    vals=[measurements[run]["par2"] for run in sets]
    reserve=measurements[sets[-1]]
    problems=[g for g in groups if any(measurements[s]["programs"][g]["counts"].get("timeout",0)==0 for s in sets[:-1]) and
         reserve["programs"][g]["counts"].get("timeout",0)==reserve["repeats"]]
    changes=[g.removeprefix("validation-") for g in problems]
    evidence={
        "title":"01б · Изменение времени default между кампаниями",
        "done":"Сверены старые и новые прогоны default для одного и того же манифеста валидации.",
        "summary":f"На тех же 11 программах PAR2 default был {vals[0]:.3f} и {vals[1]:.3f} с в прежних кампаниях и {vals[2]:.3f} с этой ночью.",
        "paragraphs":[
            "В каждой серии использован ровно тот же файл валидации и вектор default. Повторы прежних серий распределены между seed и методами поиска. Данные относятся к трём эпохам нагрузки, поэтому интервалы описывают различие на этих программах и сохраняют возможное влияние ядра, параллельной работы и частоты CPU.",
            f"Медиана wall − CPU среди решённых сеансов выросла с {measurements[sets[0]]['median_wall_cpu_gap_ms']:.1f} до {reserve['median_wall_cpu_gap_ms']:.1f} мс. Сеансы, перешедшие от решения во всех повторах хотя бы одной прежней серии к таймауту во всех ночных повторах: {', '.join(changes) or 'ни одного'}.",
            "Сравнение внутри одной новой пары контролирует общее расписание лучше, чем относительное улучшение, вычисленное между датами. Каждый эксперимент ниже использует свой измеренный default."
        ],
        "tables":[card_table("Одна и та же исходная валидация · 11 программ",
                    ["Эпоха","Повторов на программу","Всего воспроизведений","PAR2, с","Таймауты","К первой [95% ДИ, программы]"],
                    [[s,v["repeats"],v["replays"],f'{v["par2"]:.3f}',v["counts"].get("timeout",0),
                      fmt_ci(v["ratio_first"])] for s,v in result.items()]),
                  card_table("Таймауты / повторы · одна строка на исходную программу",
                       ["Программа",*sets],[[g.removeprefix("validation-"),*[f'{result[s]["programs"][g]["counts"].get("timeout",0)} / {result[s]["repeats"]}' for s in sets]]
                         for g in groups])],
        "paths":["runs/01-stability/archival-default.json","../campaign/runs/campaign-v1/full/seed-<seed>/<метод>/validation-sessions.jsonl",
                 "../interactive/runs/interactive-v1/seed-<seed>/validation-sessions.jsonl"],
        "recommendation":"Принимать решение по парным воспроизведениям с default в каждом блоке. Контролировать исходы около рабочего лимита.",
        "next":"Сравнить фиксированное меню и простой селектор на новых семействах, включая дубль default."}
    write_json(ROOT/"evidence/01z-archival.json",evidence)
    build("Исторический и новый default сверены на исходных программах. Записываю свежую разработочную выборку для портфеля.")
if __name__=="__main__":
    main()
