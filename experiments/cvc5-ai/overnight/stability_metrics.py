"""Separate repeat noise from variation across complete Java programs."""
from collections import Counter
from pathlib import Path
import sys

import numpy as np
from scipy.stats import rankdata

from replay import ROOT, lines, load
from report import write_json

B=5000

def replay_cube(path):
    plan = load(path/"plan.json")
    rows = [r for p in sorted(path.glob("worker-*.jsonl")) for r in lines(p)]
    groups = sorted({r["group"] for r in rows})
    arms = list(plan["configs"])
    by_g,by_a = ({key:i for i,key in enumerate(seq)} for seq in (groups,arms))
    shape = (len(groups),len(arms),plan["repeats"])
    score = np.full(shape,np.nan)
    status = np.empty(shape,dtype=object)
    wall = np.full(shape,np.nan)
    for r in rows:
        loc=(by_g[r["group"]],by_a[r["arm"]],r["repeat"])
        assert np.isnan(score[loc])
        score[loc]=r["par2"]
        wall[loc]=r["wall_s"]
        status[loc]=r["status"]
    assert np.isfinite(score).all()
    return plan,groups,arms,score,status,wall

def ci(values):
    return np.percentile(values,[2.5,97.5]).tolist()

def ratios(a,b,draws):
    a,b=np.asarray(a),np.asarray(b)
    return float(a.mean()/b.mean()),ci(a[draws].sum(axis=1)/b[draws].sum(axis=1))

def pct(v):
    return f"{100*v:.1f}%"

def f3(v):
    return f"{v:.3f}"

def fmt_ci(result):
    x,bounds=result
    return f"{x:.3f} [{bounds[0]:.3f}; {bounds[1]:.3f}]"

def card_table(caption, columns, rows):
    return {"caption":caption,"columns":columns,"rows":rows}

def analyze(path):
    verified=load(path/"VERIFY.json")
    plan,groups,arms,all_scores,status,all_wall=replay_cube(path)
    origins=load(ROOT/"protocols/stability.json")["origins"]
    assert groups and len(groups)==verified["programs"]==11 and len(arms)-1==len(origins)
    rng=np.random.default_rng(415822)
    g,n,r=all_scores.shape
    assert r==12
    clean=~np.isin(status,["error","incorrect","unknown"]).any(axis=(0,2))
    clean[arms.index("default_alias")]=False
    eligible=np.flatnonzero(clean)
    cube=all_scores[:,eligible,:]
    values=cube.mean(axis=2)
    scores=values.mean(axis=0)
    rank=np.argsort(scores,kind="stable")
    default=arms.index("default")
    alias=arms.index("default_alias")
    best_idx=rank[0]
    best,best_name=eligible[best_idx],arms[eligible[best_idx]]
    ref=all_scores[:,default,:]
    ref_alias=all_scores[:,alias,:]
    draws=rng.integers(g,size=(B,g))
    boot_scores=values[draws].mean(axis=1)
    prob=np.bincount(np.argmin(boot_scores,axis=1),minlength=len(eligible))/B
    subs=[]
    for count in (1,2,3,4,6,8,10,12):
        win=Counter()
        match=[]
        rho=[]
        hold=[]
        optimistic=[]
        independent=[]
        for _ in range(1 if count==12 else 1800):
            indices=rng.permutation(r)
            chosen=indices[:count]
            complement=indices[count:]
            a=cube[:,:,chosen].mean(axis=(0,2))
            a_best=int(np.argmin(a))
            win[arms[eligible[a_best]]]+=1
            match.append(a_best==best_idx)
            if len(complement):
                b=cube[:,:,complement].mean(axis=(0,2))
                hold.append(a_best==int(np.argmin(b)))
                rho.append(np.corrcoef(rankdata(a),rankdata(b))[0,1])
                optimistic.append(b[a_best]-a[a_best])
                if count<=6:
                    b2=cube[:,:,complement[:count]].mean(axis=(0,2))
                    independent.append(a_best==int(np.argmin(b2)))
        subs.append({"repeats":count,"selections":sum(win.values()),"winner_frequencies":dict(win),
                     "full_top_agreement":sum(match)/len(match),
                     "disjoint_top_agreement":float(np.mean(hold)) if hold else None,
                     "same_size_disjoint_top_agreement":float(np.mean(independent)) if independent else None,
                     "median_disjoint_spearman":float(np.median(rho)) if rho else None,
                     "mean_holdout_minus_selection_par2_s":float(np.mean(optimistic)) if optimistic else None})
    both_solved=(status[:,default,:]=="solved")&(status[:,alias,:]=="solved")
    wins={a:np.mean(all_scores[:,i,:],axis=1) for i,a in enumerate(arms)}
    under=all_scores[:,best,:]-ref
    alias_delta=ref_alias-ref
    historical_best=[a for a in arms if a in origins and any(
        q["winner"] and "smoke" not in q["lock"] for q in origins[a])]
    representative=list(dict.fromkeys(["default",best_name,
        *[arms[eligible[i]] for i in rank[:4]],*historical_best]))[:8]
    program_stats={}
    for j,group in enumerate(groups):
        program_stats[group]={a:{"par2":float(wins[a][j]),
                                 "delta_vs_default_s":float(wins[a][j]-wins["default"][j]),
                                 "ratio_to_default":float(wins[a][j]/wins["default"][j]),
                                 "counts":dict(Counter(status[j,i]))}
                              for i,a in enumerate(arms)}
    outcome={a:{"par2":float(wins[a].mean()),"ratio_and_ci":ratios(wins[a],wins["default"],draws),
                "counts":verified["counts"][a],"eligible":bool(clean[i]),
                "origins":origins.get(a,[])}
             for i,a in enumerate(arms)}
    for j,i in enumerate(eligible):
        outcome[arms[i]]["program_bootstrap_win_fraction"]=float(prob[j])
    result={"groups":groups, "repeats":r,"best":best_name,"arms":outcome,
            "program_stats":program_stats,"resampling":subs,"rng_seed":415822,
            "representative":representative,"cluster_bootstrap_samples":B,
            "alias":{"ratio_and_ci":outcome["default_alias"]["ratio_and_ci"],
                     "paired_mean_absolute_par2_s":float(np.abs(alias_delta).mean()),
                     "both_solved_pairs":int(both_solved.sum()),
                     "solved_pair_median_abs_ms":float(np.median(1000*np.abs(alias_delta[both_solved]))),
                     "solved_pair_p90_abs_ms":float(np.percentile(1000*np.abs(alias_delta[both_solved]),90)),
                     "discordant_timeout_pairs":int(np.sum((status[:,default,:]=="timeout") != (status[:,alias,:]=="timeout")))},
            "best_delta":{"cross_program_sd_s":float(under.mean(axis=1).std(ddof=1)),
                          "within_program_rms_sd_s":float(np.sqrt(np.mean(under.var(axis=1,ddof=1)))),
                          "alias_within_program_rms_sd_s":float(np.sqrt(np.mean(alias_delta.var(axis=1,ddof=1))))}}
    write_json(path/"metrics.json",result)
    total_status=Counter()
    for c in verified["counts"].values():
        total_status.update(c)
    best_count=outcome[best_name]["counts"]
    previous_winners=set(historical_best)
    s2=subs[1]
    summary=(f"На исходной валидации {best_name} набрал {f3(outcome[best_name]['par2'])} с PAR2 "
             f"против {f3(outcome['default']['par2'])} с у default. Два независимых набора по два повтора "
             f"выбирали одного победителя в {pct(s2['same_size_disjoint_top_agreement'])} разбиений.")
    def rows_scores():
        label={"default_alias":"default (дубль)"}
        for a in ["default","default_alias",*[arms[eligible[i]] for i in rank if arms[eligible[i]]!="default"],
                  *[a for i,a in enumerate(arms) if not clean[i] and a!="default_alias"]]:
            data=outcome[a]; c=data["counts"]
            yield [label.get(a,a),f3(data["par2"]),fmt_ci(data["ratio_and_ci"]),
                   c.get("timeout",0),c.get("unknown",0),c.get("error",0),c.get("incorrect",0),
                   "да" if a in previous_winners else "—"]
    med=result["alias"]["solved_pair_median_abs_ms"]
    p90=result["alias"]["solved_pair_p90_abs_ms"]
    bw=result["best_delta"]
    texts=[
        f"Исходные {g} программ, {r} повторов на ячейку и {len(arms)-1} различных векторов плюс дубль default. Всего {verified['rows']} запусков: {total_status['solved']} полных, {total_status['timeout']} таймаутов, {total_status['error']} ошибок, {total_status['unknown']} unknown и {total_status['incorrect']} несовпадений. Варианты взяты из основных и пробных шорт-листов первой кампании и основных шорт-листов Interactive Sol. Точные опции и происхождение записаны отдельно.",
        f"Конфигурации перемешивались внутри каждой пары «программа × повтор» на одном физическом ядре. Независимая проверка восстановила порядок, сверила ответы с оракулом и пересчитала штрафы. Для ранжирования исключены ошибки, unknown, несовпадения и метка-дубль.",
        f"Оценка {best_name}/default составила {fmt_ci(outcome[best_name]['ratio_and_ci'])}. Кластерный bootstrap по 11 программам, {B} выборок. Победитель выбран на тех же программах, поэтому интервал описательный и подвержен смещению выбора.",
        f"У дубля default {result['alias']['both_solved_pairs']} пар, в которых оба прогона завершились. Медиана абсолютной разницы {med:.1f} мс, 90-й процентиль {p90:.1f} мс. Разный исход по таймауту в {result['alias']['discordant_timeout_pairs']} из {g*r} пар. SD средних парных эффектов {best_name} между программами {bw['cross_program_sd_s']:.3f} с, типичное SD внутри программы {bw['within_program_rms_sd_s']:.3f} с, у дубля {bw['alias_within_program_rms_sd_s']:.3f} с.",
        f"При двух повторах для выбора и десяти оставшихся для оценки выбранный вариант теряет в среднем {1000*s2['mean_holdout_minus_selection_par2_s']:+.1f} мс PAR2. Пересэмплирование повторов оценивает шум на этих 11 программах. Bootstrap по программам показывает отдельную чувствительность к составу набора."
    ]
    tables=[
        card_table(f"Ранги при уменьшении числа повторов · те же {g} программ, до 1800 разбиений",["Повторов выбрано","Совпадение с полным лидером","Тот же победитель на равной непересекающейся части","Медиана Spearman с остатком","PAR2 на остатке минус при выборе, мс"],
                  [[s["repeats"],pct(s["full_top_agreement"]),pct(s["same_size_disjoint_top_agreement"]) if s["same_size_disjoint_top_agreement"] is not None else "—",
                    f3(s["median_disjoint_spearman"]) if s["median_disjoint_spearman"] is not None else "—",
                    f'{s["mean_holdout_minus_selection_par2_s"]*1000:+.1f}' if s["mean_holdout_minus_selection_par2_s"] is not None else "—"] for s in subs]),
        card_table(f"Программа × конфигурация · средний PAR2, с / таймауты из {r}",["Программа",*representative],
                   [[group.removeprefix("validation-"),*[f'{data[a]["par2"]:.3f} / {data[a]["counts"].get("timeout",0)}' for a in representative]]
                    for group,data in program_stats.items()]),
        card_table(f"Все настройки · по {g*r} сеанса на метку, 95% кластерный ДИ для отношения",["Вектор","PAR2, с","К default [95% ДИ]","Таймауты","Unknown","Ошибки","Несовпадения","Исторический победитель"],list(rows_scores()))
    ]
    write_json(ROOT/"evidence/01-stability.json",{
        "title":"01 · Устойчивость валидации", "done":f"Замерены и независимо проверены {verified['rows']} сеансов по {g} исходным программам.",
        "summary":summary,"paragraphs":texts,"tables":tables,
        "paths":["protocols/stability.json","runs/01-stability/plan.json","runs/01-stability/worker-{0..5}.jsonl","runs/01-stability/VERIFY.json","runs/01-stability/metrics.json"],
        "recommendation":"При сравнении близких настроек учитывать парный шум времени и смену таймаутов. Проверять выигрыш на новых программах до выбора настройки для эксплуатации.",
        "next":"Заморозить небольшое меню по исходной валидации и измерить перенос, оракул на отдельных повторах и селектор по признакам исходного кода."})
    return result

if __name__=="__main__":
    analyze(Path(sys.argv[1]))
