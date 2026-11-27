"""Compare realized training predictions on forced options measured by both arms."""
from collections import defaultdict
import sys

import numpy as np

from replay import ROOT, digest, lines, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci

ARMS = ("sol", "rf")
THRESHOLDS = (0.0, .025, .050, .100)
STUDIES = {
    "original": ("runs/05-search", (18713, 36251, 59077, 71591), 1800, "20b-forced-predictions"),
    "aligned": ("runs/20-aligned-search", (97301, 140831), 1440, "21b-forced-predictions"),
}


def extract(root, seeds, horizon):
    comparisons = []
    everything = {}
    files = {}
    for seed in seeds:
        target = root / f"seed-{seed}"
        proposal = load(target / "proposals.json")["rounds"]
        history = {}
        for arm in ARMS:
            base = target / arm
            chunks = ("decisions.jsonl", "candidates.jsonl", "sessions.jsonl", "feedback-VERIFY.json")
            files.update({str((base/p).relative_to(ROOT)):digest(base/p) for p in chunks})
            decisions = {r["round"]: r for r in lines(base/"decisions.jsonl") if r["predictions"] is not None}
            eligible = {r["candidate_id"]: r for r in lines(base/"candidates.jsonl")
                        if r["complete"] and r["eligible"] and r["finish_s"] <= horizon
                        and r["proposal"] and not r["proposal"]["ranked"] and r["proposal"]["model_ok"]}
            sessions = defaultdict(list)
            for outcome in lines(base/"sessions.jsonl"):
                if outcome["candidate_id"] in eligible:
                    sessions[outcome["candidate_id"]].append(outcome)
            measured = {}
            for idx, row in eligible.items():
                results = sessions[idx]
                assert len({r["group"] for r in results}) == len(results) == 15
                assert not {r["status"] for r in results} - {"timeout", "solved"}
                assert abs(row["par2"] - np.mean([q["par2"] for q in results]))<1e-12
                pos = row["proposal"]["index"]
                round_id = row["proposal"]["round"]
                assert row["config"] == proposal[round_id]["pool"][pos]
                assert pos in proposal[round_id]["forced"]
                measured[round_id, pos] = row["par2"]
            history[arm] = (decisions, measured)
        for j, chunk in enumerate(proposal):
            key = chunk["forced"]
            assert len(key) == 2 and len(set(key)) == 2
            if not all(j in history[a][0] and all((j,k) in history[a][1] for k in key) for a in ARMS):
                continue
            predicted, measured = {}, {}
            for a in ARMS:
                d, m = history[a]
                assert d[j]["forced"] == key
                predicted[a] = [d[j]["predictions"][k] for k in key]
                measured[a] = [m[j,k] for k in key]
            overlap = np.mean([measured[a] for a in ARMS],axis=0).tolist()
            comparisons.append({"seed":seed,"round":j,"forced":key,"predicted":predicted,
                                "measured":measured,"reference":overlap})
        values = [c for c in comparisons if c["seed"] == seed]
        everything[str(seed)] = {"rounds": len(values), "prediction_rounds": {a: len(history[a][0]) for a in ARMS}}
    assert comparisons
    return comparisons, everything, files


def evaluate(rows):
    rng = np.random.default_rng(350902)
    ref = np.array([row["reference"] for row in rows])
    diff = ref[:,0] - ref[:,1]
    predicted = {a: np.array([row["predicted"][a] for row in rows]) for a in ARMS}
    measured = {a: np.array([row["measured"][a] for row in rows]) for a in ARMS}
    role = {a: np.sign(measured[a][:,0]-measured[a][:,1]) for a in ARMS}
    stable = role["sol"] == role["rf"]
    ranked = {a: np.sign(predicted[a][:,0]-predicted[a][:,1]) for a in ARMS}
    assignments = np.array([row["seed"] for row in rows])
    seed_index = sorted(set(assignments))
    draws = rng.integers(len(seed_index),size=(5000,len(seed_index)))
    quality = {}
    for threshold in THRESHOLDS:
        eligible = np.abs(diff)>threshold
        for restrict in (True, False):
            where = eligible & stable if restrict else eligible
            if not where.any():
                continue
            hit = {a: np.where(ranked[a] == 0, .5, (ranked[a] == np.sign(diff)).astype(float)) for a in ARMS}
            changes = hit["sol"] - hit["rf"]
            within = np.array([changes[where & (assignments==seed)].sum() for seed in seed_index])
            available = np.array([(where & (assignments==seed)).sum() for seed in seed_index])
            sample_den = available[draws].sum(axis=1)
            resampled = within[draws].sum(axis=1)[sample_den>0] / sample_den[sample_den>0]
            tag = f"{threshold:.3f}/"+("agreed" if restrict else "average")
            quality[tag] = {"n":int(where.sum()),**{a:float(hit[a][where].mean()) for a in ARMS},
                            "sol_minus_rf":[float(changes[where].mean()),np.percentile(resampled,[2.5,97.5]).tolist()]}
    return {
        "matching_rounds":len(rows),"candidate_pairs":len(rows),"options_measured_per_arm":len(rows)*2,
        "rank_agreement_between_measurement_streams":int(stable.sum()),
        "median_abs_case_gap_ms":float(np.median(np.abs(measured["sol"]-measured["rf"]))*1000),
        "absolute_error_s":{a:float(np.abs(predicted[a]-ref).mean()) for a in ARMS},
        "mean_error_s":{a:float((predicted[a]-ref).mean()) for a in ARMS},
        "ranking":quality,"training_programs":15,"repeats_per_option":2,"distinct_search_seeds":len(seed_index),
    }


def main(kind):
    path,seeds,limit,card = STUDIES[kind]
    base = ROOT / path
    out = base / "forced-predictions"
    out.mkdir(exist_ok=True)
    plan = {
        "locked":now(),"roots":path,"search_seeds":seeds,"horizon_seconds":limit,
        "primary_threshold_s":.050,"sensitivity_thresholds_s":THRESHOLDS,
        "definition":"The two exploration options are forced by the common pool, independent of the current ranking. Each arm predicted scores before measuring; require the actual complete eligible measurements from both. Reference is mean of two fresh training measurements for each vector. In 'agreed', both measurement streams must have the same ordering.",
        "purpose":"Retrospective audit of rank utility on training, not test or deployment efficacy. Reuses exact immutable measurements. No new model calls. Seed-resampled uncertainty conditional on the 15 common programs.",
    }
    if not (out/"protocol.json").exists():
        write_json(out/"protocol.json",plan)
    rows, seed_receipts, hashes = extract(base,seeds,limit)
    evidence = evaluate(rows)
    write_json(out/"paired-scores.json",rows)
    write_json(out/"metrics.json",{**evidence,"raw_hashes":hashes,"seeds":seed_receipts})
    focus = evidence["ranking"].get("0.050/average", evidence["ranking"]["0.000/average"])
    summary = (f"В {focus['n']} общих принудительных парах ранжирование совпало с замером у Sol в "
               f"{100*focus['sol']:.1f}%, у RF в {100*focus['rf']:.1f}%.")
    rows = [[key.split("/")[0], "оба замера" if key.endswith("agreed") else "средний замер",
             q["n"], f"{100*q['sol']:.1f}%", f"{100*q['rf']:.1f}%",
             fmt_ci([100*q["sol_minus_rf"][0],[100*v for v in q["sol_minus_rf"][1]]])]
            for key,q in evidence["ranking"].items()]
    title = "21б · Предсказания при согласованной разминке" if kind=="aligned" else "20б · Проверка предсказаний Sol и RF по общим пробам"
    write_json(ROOT/"evidence"/(card+".json"),{
        "title":title,"done":f"Сопоставлены предсказания на {evidence['matching_rounds']} общих принудительных парах.",
        "summary":summary,
        "paragraphs":[
            f"Пара задаётся двумя принудительными вариантами, которые выбирал общий генератор до оценки Sol или RF. {evidence['candidate_pairs']} пар, {evidence['options_measured_per_arm']} измерений вариантов на ветку. Во всех ветках те же 15 обучающих программ. На вариант два фактических измерения, по одному на ветку. Seed поиска {evidence['distinct_search_seeds']}.",
            f"Средняя абсолютная ошибка прогноза PAR2 у Sol {evidence['absolute_error_s']['sol']:.3f} с, у RF {evidence['absolute_error_s']['rf']:.3f} с. Медиана расхождения фактических замеров одного вектора {evidence['median_abs_case_gap_ms']:.1f} мс. Две ветки согласились с порядком измерений в {evidence['rank_agreement_between_measurement_streams']} из {evidence['candidate_pairs']} пар.",
            "Это расчёт на обучении по уже сделанным измерениям. Он проверяет ранжирование случайных точек за пределами приоритетной части раунда. Строки различаются порогом по фактической разнице и требованием согласия двух оценок. ДИ по seed условен на этих 15 программах.",
        ],
        "tables":[card_table("Ранжирование пар · разность Sol − RF в процентных пунктах",
                   ["Минимальная разница PAR2, с","Как сопоставлены факты","Пар","Sol","RF","Sol − RF [95% ДИ]"],rows)],
        "paths":[str((out/f).relative_to(ROOT)) for f in ("protocol.json","paired-scores.json","metrics.json")],
        "recommendation":"Раздельно оценивать точность предложений, стоимость прогноза и результат фиксированного закрытого теста.",
        "next":"Проверить, изменяет ли согласованная разминка практическую разницу качества рейтинга.",
    })
    build(load(ROOT/"current.json")["current"])


if __name__=="__main__":
    main(sys.argv[1])
