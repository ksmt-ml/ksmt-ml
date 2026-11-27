"""Cross solver randomness and option choice on a separate unseen family cohort."""
from collections import Counter
from pathlib import Path

import numpy as np

from corpus import materialize, seal
from portfolio import MENU, compatible, frac, group_draws, smoke_matrix
from replay import CORES, ROOT, digest, launch, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, pct, replay_cube

BASE=ROOT/"runs/07-solver-seeds"
SCOPE=ROOT/"protocols/solver-seeds.json"
SEEDS=(18473,1721,9551)
REPEATS=6

def setup():
    plan=seal("solver-seed-test",22,2,28)
    if SCOPE.exists():
        return load(SCOPE)
    configs={"default":{}}
    menu=load(MENU)
    candidates=[("global",menu["arms"][menu["global_arm"]]),
                ("joint",{"simplification":"none","bv-sat-solver":"cadical"})]
    calibration=ROOT/"runs/06-cap-calibration/replay/metrics.json"
    if calibration.exists():
        m=load(calibration)
        q=load(ROOT/"protocols/cap-calibration.json")
        rank=m["by_budget"]["4.5"]["arms"]
        name=min((a for a in rank if a!="default_alias"),key=lambda a:(rank[a]["par2"],a))
        candidates.append(("long_best",q["configs"][name]))
    for key,value in candidates:
        value=compatible(value)
        if value not in configs.values():
            configs[key]=value
    sm=smoke_matrix(configs,"07-solver-seed-smoke")
    assert len(sm["pass"])==len(configs)
    record={"locked":now(),"hypothesis":"If option gains are solver-seed dependent, the same program-optimal configuration will transport poorly even across seeds on identical inputs.",
            "solver_seeds":SEEDS,"repeats":REPEATS,"configs":configs,"corpus_commit":plan["seed_commitment_sha256"],
            "options_selected_from":"fixed portfolio and 2x2 factorial only, before independent seed release",
            "objective":"ratios to default at each solver seed; per-program oracle selected on first half of seed 18473 and evaluated on independent repeats at each seed; in-seed oracle crossfit control",
            "bootstrap":"whole Java generation family; repeat timings within each program × setting × solver seed",
            "input_hashes":{"portfolio":digest(MENU),"calibration":digest(calibration) if calibration.exists() else None,
                            "smoke":digest(ROOT/"runs/07-solver-seed-smoke/VERIFY.json")}}
    write_json(SCOPE,record)
    SCOPE.chmod(0o444)
    return record

def analyze(run,manifest,record):
    verified=load(run/"VERIFY.json")
    _,groups,arms,scores,st,wall=replay_cube(run)
    source=load(manifest)
    fam={r["group"]:r["family"] for r in source["sessions"]}
    families=[fam[g] for g in groups]
    P,A,R=scores.shape
    assert R==REPEATS
    keys=list(record["configs"])
    J=np.arange(P)
    drawn=group_draws(families,np.random.default_rng(8142))
    seeds=record["solver_seeds"]
    each={z:scores[:,[arms.index(f"{k}@{z}") for k in keys],:] for z in seeds}
    alt_index=[arms.index(f"default@{seeds[0]}"),arms.index(f"default_alias@{seeds[0]}")]
    pair=(st[:,alt_index[0],:]=="solved")&(st[:,alt_index[1],:]=="solved")
    ms=1000*np.median(np.abs(wall[:,alt_index[0],:]-wall[:,alt_index[1],:])[pair])
    h=R//2
    sel0=np.argmin(each[seeds[0]][:,:,:h].mean(axis=2),axis=1)
    effects={}
    for z,Y in each.items():
        s=Y.mean(axis=2)
        first=Y[:,:,:h].mean(axis=2)
        second=Y[:,:,h:].mean(axis=2)
        pick_this_first=np.argmin(first,axis=1)
        pick_this_second=np.argmin(second,axis=1)
        into=second[J,sel0]
        baseline=second[:,0]
        cross=(first[J,pick_this_second]+second[J,pick_this_first])/2
        effects[str(z)]={"arms":{name:{"par2":float(s[:,ia].mean()),
                                   "ratio":frac(s[:,ia],s[:,0],drawn),
                                   "counts":verified["counts"][f"{name}@{z}"]}
                                for ia,name in enumerate(keys)},
                         "fraction_matching_first_half_oracle":float(np.mean(pick_this_first==sel0)),
                         "oracle_transferred":frac(into,baseline,drawn),
                         "oracle_within":frac(cross,s[:,0],drawn),
                         "oracle_separate_halves_ranking_compared":z!=seeds[0],
                         "groups":{g:{"family":fam[g],"selected_from_seed":keys[sel0[i]],
                                   "own_first_choice":keys[pick_this_first[i]],
                                   "ratios_to_default":{k:float(s[i,ia]/s[i,0]) for ia,k in enumerate(keys)}}
                                    for i,g in enumerate(groups)}}
    result={"programs":P,"families":len(set(families)),"repeats":R,"rejected":len(source["rejected"]),
            "runs":verified["rows"],"median_alias_abs_ms":float(ms),"pairs_alias_solved":int(pair.sum()),
            "effects":effects,"family_bootstrap_count":B,"seed_analysis":"controlled fixed seed settings"}
    write_json(run/"metrics.json",result)
    N=seeds[0]
    examples=[[z,key,f'{vals["par2"]:.3f}',fmt_ci(vals["ratio"]),vals["counts"].get("timeout",0),
                sum(vals["counts"].get(status,0) for status in ("error","unknown","incorrect"))]
               for z,rows in effects.items() for key,vals in rows["arms"].items()]
    ratios=[effects[str(z)]["oracle_transferred"][0] for z in seeds]
    summ=(f"У оракула, выбравшего опцию при seed {N}, отношения к default "
          f"при трёх seed решателя составили {', '.join(f'{x:.3f}' for x in ratios)}.")
    write_json(ROOT/"evidence/08-solver-seeds.json",{
        "title":"08 · Устойчивость к внутреннему seed cvc5",
        "done":f"Проверены {verified['rows']} воспроизведений с тремя seed решателя.",
        "summary":summ,
        "paragraphs":[
            f"Новый независимый тест, {P} Java-программ из {len(set(families))} семейств, {R} повторов на пару «настройка × seed решателя». Всего {verified['rows']} запусков, {len(source['rejected'])} трасс исключено до воспроизведения. Это три внутренних seed cvc5 при фиксированных входах. Seed оптимизатора здесь не участвует.",
            f"Оракул выбирает флаг по первым {h} повторам при seed {N} и оценивается на отдельных {h} повторах каждого seed. Контрольный оракул внутри seed меняет местами половины. Главное сравнение измеряет, переносится ли выигрыш даже при неизменном целевом сеансе. 95% ДИ парно пересэмплируют семейства.",
            f"Медиана |default − дубль| равна {ms:.1f} мс в {int(pair.sum())} полных парах при основном seed. Оставшиеся seed назначены заранее. Ячейки случайно чередовали на одном ядре."
        ],
        "tables":[card_table(f"Внутренний seed и флаги · по {P*R} воспроизведений на строку",
                  ["Seed","Флаги","PAR2, с","К default [95% ДИ]","Таймауты","Ошибки + unknown + несовпадения"],examples),
                  card_table("Оракул под сменой внутреннего seed · оценка на других повторах",
                  ["Seed оценки","К default [95% ДИ], перенос","К default [95% ДИ], выбор внутри seed","Тот же выбор на первых повторах"],
                  [[z,fmt_ci(row["oracle_transferred"]),fmt_ci(row["oracle_within"]),
                    pct(row["fraction_matching_first_half_oracle"]) if z!=str(N) else "тот же набор"]
                   for z,row in effects.items()])],
        "paths":["protocols/solver-seeds.json","runs/07-solver-seeds/corpus/manifest.json",
                 "runs/07-solver-seeds/replay/worker-{0..5}.jsonl","runs/07-solver-seeds/replay/metrics.json",
                 "runs/07-solver-seeds/replay/VERIFY.json"],
        "recommendation":"Сравнивать практический выигрыш с изменчивостью решения при смене seed и лимита сеанса.",
        "next":"Проверить, улучшают ли перенос признаки префикса SMT до первой проверки при том же зафиксированном меню."
    })
    build()

def main():
    record=setup()
    build("Измеряю перенос настроек между тремя внутренними seed cvc5 на новом наборе.")
    target=BASE/"corpus"
    m=target/"manifest.json"
    if not m.exists():
        precedents=sorted({p.parent for p in (ROOT/"runs").glob("**/programs.json") if target not in p.parents})
        m=materialize("solver-seed-test",SCOPE,target,precedents,core=CORES[0])
    configs={f"{k}@{z}":p for z in SEEDS for k,p in record["configs"].items()}
    configs[f"default_alias@{SEEDS[0]}"]={}
    solver_seeds={arm:int(arm.split("@")[1]) for arm in configs}
    out=BASE/"replay"
    if not (out/"VERIFY.json").exists():
        launch(out,m,configs,REPEATS,567801,3200,solver_seed_by_arm=solver_seeds)
    analyze(out,m,record)
if __name__=="__main__":
    from recovery import restore_prerequisites
    restore_prerequisites()
    main()
