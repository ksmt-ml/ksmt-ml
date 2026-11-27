"""Compare validation breadth versus repeated timing at equal trial counts."""
from collections import Counter
import math
from pathlib import Path

import numpy as np
from scipy.stats import rankdata

from corpus import materialize, seal
from portfolio import MENU, compatible, frac, group_draws, smoke_matrix
from replay import CORES, ROOT, digest, launch, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, pct, replay_cube

BASE=ROOT/"runs/15-allocation"
SCOPE=ROOT/"protocols/validation-allocation-menu.json"
LOCK=ROOT/"protocols/validation-allocation-decisions.json"
DEV_REPEATS=12
TEST_REPEATS=8
PARTITIONS=1500

def priors(target):
    return sorted({p.parent for p in (ROOT/"runs").glob("**/programs.json") if target not in p.parents})

def freeze():
    seal("allocation-dev",52,1,36)
    seal("allocation-test",48,1,32)
    if SCOPE.exists():
        return load(SCOPE)
    menu=load(MENU)
    ranks=sorted(menu["validation_par2"],key=lambda k:(menu["validation_par2"][k],k))
    names=list(dict.fromkeys(["default",menu["global_arm"],*ranks]))[:5]
    configs={a:compatible(menu["arms"][a]) for a in names}
    certified=smoke_matrix(configs,"15-allocation-smoke")
    assert set(certified["pass"])==set(configs)
    s={"locked":now(),"why":"Legacy 2-repeat leader was unstable, and little gain remained after repeat correction. Measure how to spend a fixed validation evaluation count: broad family coverage versus repeats per family.",
       "source":"Same frozen USVM generator, new mutually exclusive operation families, single program per family.",
       "configs":configs,"frozen_global":menu["global_arm"],"menu_sha256":digest(MENU),
       "main_design":"Paired random measurements on full development matrix. Compare subsets sharing N × repeats = 3 N_max solve calls per arm, where N_max is largest multiple of four among audited development families.",
       "alternatives":"N_max/4 × 12, N_max/2 × 6, N_max × 3. Select a single winner per sampled split, then freeze distributions across choices before independent test. Holdout reports expected performance per scheme and primary frozen global.",
       "dev_repeats":DEV_REPEATS,"test_repeats":TEST_REPEATS,"partitions":PARTITIONS,
       "budget_contract":"equal completed replay counts, not equal real time; report average wall cost of the actual reused observations for each allocation",
       "date_cutoff":"2026-11-22T10:48:00+03:00","smoke_sha256":digest(ROOT/"runs/15-allocation-smoke/VERIFY.json")}
    write_json(SCOPE,s)
    SCOPE.chmod(0o444)
    return s

def choose(dev,manifest,scope):
    if LOCK.exists():
        return load(LOCK)
    _,groups,names,score,st,wall=replay_cube(dev)
    n,a,r=score.shape
    assert r==DEV_REPEATS
    assert n==len({rec["family"] for rec in load(manifest)["sessions"]})
    nmax=4*(n//4)
    configs=dict(scope["configs"])
    assert set(names)==set(configs)
    assert not np.isin(st,["incorrect","error","unknown"]).any()
    schemes=[]
    rng=np.random.default_rng(133870313)
    group_order=np.stack([rng.permutation(n) for _ in range(PARTITIONS)])
    repeat_order=np.stack([rng.permutation(r) for _ in range(PARTITIONS)])
    prior=score.mean(axis=(0,2))
    groups_prior=np.argsort(prior,kind="stable")
    for name,num,repeats in (("deep",nmax//4,12),("balanced",nmax//2,6),("wide",nmax,3)):
        chosen=Counter()
        out_score,estimate,rank_agreement,wall_budget=[],[],[],[]
        for p_order,r_order in zip(group_order,repeat_order,strict=True):
            covered=p_order[:num]
            repeat_sel=r_order[:repeats]
            obs=score[covered][:,:,repeat_sel].mean(axis=(0,2))
            elapsed=wall[covered][:,:,repeat_sel].sum()
            winner=int(np.argmin(obs))
            chosen[names[winner]]+=1
            estimate.append(obs[winner])
            rank_agreement.append(np.corrcoef(rankdata(prior),rankdata(obs))[0,1])
            wall_budget.append(elapsed)
            complement=p_order[num:]
            if complement.size:
                less=score[complement].mean(axis=(0,2))
                out_score.append(less[winner]-less[names.index(scope["frozen_global"])])
        s={"mode":name,"distinct_programs":num,"repeats":repeats,"cells_per_config":num*repeats,
           "choices":dict(chosen),"weights":{a:chosen[a]/PARTITIONS for a in names},
           "selection_par2":float(np.mean(estimate)),"descriptive_rank_corr":float(np.median(rank_agreement)),
           "observed_measurement_wall_seconds_all_configs":float(np.mean(wall_budget)),
           "heldback_development_delta_s":float(np.mean(out_score)) if out_score else None}
        schemes.append(s)
    outcome={"locked":now(),"menu_sha256":digest(SCOPE),"development_sha256":digest(dev/"VERIFY.json"),
             "dev_manifest_sha256":digest(manifest),"programs":n,"repeats_per_cell":r,
             "menu":configs,"original_global":scope["frozen_global"],"partitions":PARTITIONS,
             "development_full_winner":names[groups_prior[0]],"schemes":schemes}
    write_json(LOCK,outcome)
    LOCK.chmod(0o444)
    return outcome

def assess(folder,manifest,scope,locked):
    plan,groups,arms,score,st,wall=replay_cube(folder)
    n,a,r=score.shape
    m=load(manifest)
    by_group={row["group"]:row["family"] for row in m["sessions"]}
    fam=[by_group[g] for g in groups]
    assert n==len(set(fam))
    M=score.mean(axis=2)
    reference=M[:,arms.index(locked["original_global"])]
    default=M[:,arms.index("default")]
    cells={}
    counters={}
    draw=group_draws(fam,np.random.default_rng(177481))
    results=[]
    for policy in locked["schemes"]:
        weights=np.array([policy["weights"].get(v,0) for v in arms])
        assert abs(weights.sum()-1)<1e-10
        expected=M@weights
        name=policy["mode"]
        outcome={"vs_original_global":frac(expected,reference,draw),"vs_default":frac(expected,default,draw),
                 "par2":float(expected.mean()),"weights":policy["weights"],
                 "expected_timeouts":float(sum(weights[i]*np.sum(st[:,i]=="timeout") for i in range(a))),
                 "expected_errors":float(sum(weights[i]*np.sum(np.isin(st[:,i],["incorrect","error","unknown"])) for i in range(a)))}
        cells[name]=expected
        counters[name]=outcome
        results.append([name,policy["distinct_programs"],policy["repeats"],policy["cells_per_config"],
                        max(policy["weights"],key=policy["weights"].get),
                        pct(max(policy["weights"].values())),f'{policy["observed_measurement_wall_seconds_all_configs"]:.1f}',
                        f'{outcome["par2"]:.3f}',fmt_ci(outcome["vs_original_global"]),f'{outcome["expected_timeouts"]:.1f}'])
    wide_diff=frac(cells["wide"],cells["deep"],draw)
    verified=load(folder/"VERIFY.json")
    record={"programs":n,"families":n,"repeats":r,"physical_replay_rows":verified["rows"],
            "development_programs":locked["programs"],"schemes":counters,"wide_vs_deep":wide_diff,
            "new_test_group_effect":{g:{k:float(v[i]) for k,v in cells.items()} for i,g in enumerate(groups)}}
    write_json(folder/"metrics.json",record)
    summary=f"При равном числе запусков на валидации широкая схема дала {fmt_ci(wide_diff)} PAR2 к схеме с большим числом повторов на {n} новых семьях."
    write_json(ROOT/"evidence/16-allocation.json",{
        "title":"16 · Больше программ или больше повторов",
        "done":"Сравнены три схемы расходования одинакового числа валидационных воспроизведений на отдельном закрытом тесте.",
        "summary":summary,
        "paragraphs":[
            f"Новая разработка содержит {locked['programs']} программ, по одной на семейство, {DEV_REPEATS} повторов на ячейку. Полные измерения позволяют независимо выбирать несколько разбиений. Для каждой схемы фиксированы 1 500 воспроизводимых отборов и одинаковое произведение числа семейств на количество повторов.",
            f"Каждое разбиение выбирает минимум PAR2. Доли победителей фиксируются до записи другого закрытого теста, {n} новых программ и {r} повторов. На закрытом тесте эти доли взвешивают уже измеренные настройки, чтобы оценить ожидаемую стоимость стратегий. Количество повторных разбиений не считается числом независимых программ.",
            "При одинаковом числе запусков различаются реальные затраты времени. Колонка wall отражает сумму измеренных длительностей на разработке по всем настройкам. Доверительные интервалы получены по новым семействам для зафиксированных долей выбора.",
        ],
        "tables":[card_table(f"Схемы валидации · {locked['programs']} разработочных семейства, {n} закрытых",
                  ["Схема","Семейств при выборе","Повторов","Запусков на настройку","Чаще всего выбиралась","Доля",
                   "Wall, с","Тест PAR2, с","К глобальной настройке [95% ДИ]","Ожидаемые таймауты / "+str(n*r)],results)],
        "paths":["protocols/validation-allocation-menu.json","protocols/validation-allocation-decisions.json",
                 "runs/15-allocation/dev-replay/VERIFY.json","runs/15-allocation/test-replay/VERIFY.json",
                 "runs/15-allocation/test-replay/metrics.json"],
        "recommendation":"Распределять бюджет валидации по измеренной устойчивости выбора и закрытому результату, сохраняя независимые программные семейства.",
        "next":"Проверить выбранную схему как фиксированное правило отбора на живом целевом клиенте."
    })
    build()

def main():
    scope=freeze()
    dev=BASE/"dev-corpus"
    before=dev/"manifest.json"
    if not before.exists():
        before=materialize("allocation-dev",SCOPE,dev,priors(dev),core=CORES[0])
    rdev=BASE/"dev-replay"
    if not (rdev/"VERIFY.json").exists():
        launch(rdev,before,scope["configs"],DEV_REPEATS,729103,2500)
    locked=choose(rdev,before,scope)
    build("Схемы расходования бюджета валидации зафиксированы. Проверяю их на отдельных новых семействах.")
    test=BASE/"test-corpus"
    manifest=test/"manifest.json"
    if not manifest.exists():
        manifest=materialize("allocation-test",LOCK,test,priors(test),core=CORES[0])
    run=BASE/"test-replay"
    if not (run/"VERIFY.json").exists():
        launch(run,manifest,scope["configs"],TEST_REPEATS,293815,2100)
    assess(run,manifest,scope,locked)
if __name__=="__main__":
    from recovery import attempt
    from search_retest import test_retest
    if (ROOT/"runs/14b-search-retiming/metrics.json").exists():
        attempt("search-retiming-confirm",test_retest)
    main()
