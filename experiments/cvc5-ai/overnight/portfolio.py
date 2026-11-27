"""Frozen configuration menu, family-separated static selector, and cross-repeat oracle."""
from collections import Counter
import math
import os
from pathlib import Path
import re
import sys
import time

import numpy as np

from corpus import materialize
from replay import CAMPAIGN, DATA, ROOT, CORES, check_budget, digest, ev, launch, load, log, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, pct, ratios, replay_cube

BASE=ROOT/"runs/02-portfolio"
MENU=ROOT/"protocols/portfolio-menu.json"
LOCK=ROOT/"protocols/portfolio-selector.json"
FEATURES=("input_bits","branches","nonconstant_mul","mul","mod","variable_shift","shift","bitlogic","width_of_literal")
REPEATS=12

def compatible(params):
    """Shared option validation for all new comparisons."""
    normalized=ev.config(params)
    if normalized.get("ite-simp")=="on":
        raise ValueError("ite-simp=on conflicts with unsat-assumption production")
    return normalized

def smoke_matrix(configs, name):
    out=ROOT/"runs"/name
    if (out/"VERIFY.json").exists():
        return load(out/"VERIFY.json")
    out.mkdir(parents=True,exist_ok=False)
    original=DATA/"train/manifest.json"
    m=load(original)
    chosen=sorted([r for r in m["sessions"] if r["operations"].get("unsatCore") and r["operations"].get("model")],
                  key=lambda r:len(r["oracle"]))[:2]
    assert len(chosen)==2
    core=CORES[0]
    pid=os.sched_getaffinity(0)
    result={}
    rng=np.random.default_rng(23801)
    order=list(configs)
    try:
        os.sched_setaffinity(0,{core})
        for r in chosen:
            rng.shuffle(order)
            for arm in order:
                check_budget()
                row=ev.measure(original,r,compatible(configs[arm]),ev.TUNING_TIMEOUT,time.monotonic()+3)
                result.setdefault(arm,[]).append(row)
    finally:
        os.sched_setaffinity(0,pid)
    counts={arm:dict(Counter(row["status"] for row in rows)) for arm,rows in result.items()}
    write_json(out/"plan.json",{"core":core,"order_rng":23801,"manifest":str(original.relative_to(CAMPAIGN)),"groups":[r["group"] for r in chosen],"configs":configs})
    write_json(out/"rows.json",result)
    write_json(out/"VERIFY.json",{"counts":counts,"programs":len(chosen),"repeats":1,
               "pass":[arm for arm,c in counts.items() if not (c.get("error") or c.get("incorrect") or c.get("unknown"))]})
    return load(out/"VERIFY.json")

def menu():
    if MENU.exists():
        return load(MENU)
    path=ROOT/"runs/01-stability"
    plan,groups,arms,cube,status,_=replay_cube(path)
    assert (path/"VERIFY.json").exists()
    candidates={}
    for index,arm in enumerate(arms):
        if arm=="default_alias" or np.isin(status[:,index,:],["error","unknown","incorrect"]).any():
            continue
        try:
            candidates[arm]=compatible(plan["configs"][arm])
        except ValueError:
            pass
    scores={arm:cube[:,arms.index(arm),:].mean(axis=1) for arm in candidates}
    ranked=sorted(candidates,key=lambda arm:(scores[arm].mean(),arm))
    origins=load(ROOT/"protocols/stability.json")["origins"]
    previous_smac=[arm for arm in ranked if any(o["arm"]=="smac" and o["winner"] and
                   "/full/" in o["lock"] for o in origins[arm])]
    smoke_names=list(dict.fromkeys(["default",*ranked[:7],*previous_smac]))
    incumbent_subset=["default",*ranked[:3]]
    remaining=set(candidates)-set(incumbent_subset)
    minima=np.minimum.reduce([scores[arm] for arm in incumbent_subset])
    complements=[]
    for _ in range(4):
        pick=min(remaining,key=lambda a:(np.minimum(minima,scores[a]).mean(),scores[a].mean(),a))
        complements.append(pick)
        minima=np.minimum(minima,scores[pick])
        remaining.remove(pick)
    smoke_names=list(dict.fromkeys([*smoke_names,*complements]))
    checked=smoke_matrix({arm:candidates[arm] for arm in smoke_names},"02-menu-smoke")
    good=set(checked["pass"])
    ranked=[arm for arm in ranked if arm in good]
    assert ranked and "default" in good
    global_arm=ranked[0]
    selected=list(dict.fromkeys(["default",global_arm,*ranked[:3],*previous_smac[:1],*complements]))
    selected=[arm for arm in selected if arm in good][:8]
    configs={arm:candidates[arm] for arm in selected}
    configs["default_alias"]={}
    data={"locked":now(),"rules":["only original validation results and historical arm winners",
              "keep default, best global, top three, first prior SMAC winner and greedily complementary arms, deduplicate and cap at eight",
              "require observed validation validity and new unsat-assumptions smoke; do not permit ite-simp=on"],
          "validation_metrics_sha256":digest(path/"metrics.json"),
          "smoke_sha256":digest(ROOT/"runs/02-menu-smoke/VERIFY.json"),
          "global_arm":global_arm,"arms":configs,"validation_programs":len(groups),
          "validation_par2":{a:float(scores[a].mean()) for a in selected},
          "source_features":list(FEATURES),"no_outcome_admission":True}
    write_json(MENU,data)
    MENU.chmod(0o444)
    return data

def features(path):
    source=Path(path).read_text()
    input_mask=int(re.search(r"int x = input & (\d+)",source)[1])
    values=[int(s) for s in re.findall(r"\b\d+\b",source)]
    return [input_mask.bit_length(),len(re.findall(r"\bif\b",source)),
            len(re.findall(r"\*\s*\(",source)),source.count("*"),source.count("%"),
            len(re.findall(r"<<\s*\(",source)),len(re.findall(r">>>|<<",source)),
            len(re.findall(r"[&|^]",source)),max(values).bit_length()]

def inputs(manifest,groups):
    m=load(manifest)
    source_by_hash={r["sha256"]:r for r in load(manifest.parent/"programs.json")["programs"]}
    row_by_group={r["group"]:r for r in m["sessions"]}
    records=[row_by_group[g] for g in groups]
    result=[]
    start=time.perf_counter_ns()
    for row in records:
        source=manifest.parent/source_by_hash[row["source_sha256"]]["source"]
        result.append(features(source))
    micros=(time.perf_counter_ns()-start)/1000/len(groups)
    return np.array(result,dtype=float),[r["family"] for r in records],micros

def fit(x,y,lam):
    mean=x.mean(axis=0)
    sd=x.std(axis=0)
    sd[sd==0]=1
    zx=(x-mean)/sd
    bias=y.mean(axis=0)
    coefs=np.linalg.solve(zx.T@zx+lam*np.eye(x.shape[1]),zx.T@(y-bias))
    return dict(mean=mean.tolist(),sd=sd.tolist(),coef=coefs.tolist(),bias=bias.tolist(),l2=float(lam))

def predict(model,x):
    return np.clip(((x-model["mean"])/model["sd"]) @ model["coef"]+model["bias"],0,3)

def choose(pred,glob,gate):
    selected=np.argmin(pred,axis=1)
    gain=pred[:,glob]-pred[np.arange(len(pred)),selected]
    return np.where(gain>gate,selected,glob)

def fold_lock(directory, manifest, data):
    if LOCK.exists():
        return load(LOCK)
    plan,groups,arms,scores,status,_=replay_cube(directory)
    drop=arms.index("default_alias")
    assert np.isfinite(scores).all()
    eligible=[a for i,a in enumerate(arms) if i!=drop and not np.isin(status[:,i,:],["error","incorrect","unknown"]).any()]
    if data["global_arm"] not in eligible or "default" not in eligible:
        raise RuntimeError("primary portfolio arm failed development eligibility check")
    y=scores[:,[arms.index(a) for a in eligible],:].mean(axis=2)
    glob=eligible.index(data["global_arm"])
    x,families,micros=inputs(manifest,groups)
    f=np.array(families)
    cv=[]
    # Leave out an entire operation family. No program shares a skeleton with its training fold.
    for lam in (1.0,8.0,32.0):
        pred=np.zeros_like(y)
        for family in sorted(set(families)):
            test=f==family
            pred[test]=predict(fit(x[~test],y[~test],lam),x[test])
        for gate in (0.0,.03,.10,.20):
            chosen=choose(pred,glob,gate)
            avg=float(y[np.arange(len(y)),chosen].mean())
            cv.append({"l2":lam,"gate_s":gate,"predictions":chosen.tolist(),"par2":avg,
                       "switches":int(np.sum(chosen!=glob))})
    # Predeclared decision uses a 10 ms gain on development CV to prefer a
    # more complex policy over the selected global. Always report bare ridge as secondary.
    best=min(cv,key=lambda r:(r["par2"],-r["gate_s"],-r["l2"]))
    baseline=float(y[:,glob].mean())
    select_global=best["par2"]>=baseline-.010
    full=fit(x,y,best["l2"])
    lock={"locked":now(),"menu_sha256":digest(MENU),"dev_manifest_sha256":digest(manifest),
          "dev_replay_sha256":digest(directory/"VERIFY.json"),"fit":full,
          "gate_s":best["gate_s"],"backoff_to_global":select_global,"global":data["global_arm"],
          "eligible":eligible,"cv_results":cv,"cv_global_par2":baseline,
          "cv_best_par2":best["par2"],"cv_chosen":best["predictions"],"cv_groups":groups,"cv_families":families,
          "dev_feature_us_per_program":micros,"features":list(FEATURES),
          "disqualified_from_selector":[a for a in arms if a not in eligible and a!="default_alias"]}
    write_json(LOCK,lock)
    LOCK.chmod(0o444)
    return lock

def group_draws(families,rng):
    clusters=[[i for i,val in enumerate(families) if val==family] for family in sorted(set(families))]
    selected=rng.integers(len(clusters),size=(B,len(clusters)))
    # Multiplicities let clusters vary in size, while weighting programs equally.
    matrix=np.zeros((B,len(families)),dtype=int)
    for j,indices in enumerate(clusters):
        matrix[:,indices]=np.sum(selected==j,axis=1)[:,None]
    return matrix

def frac(a,b,matrix):
    a,b=np.asarray(a),np.asarray(b)
    vals=np.sum(matrix*a,axis=1)/np.sum(matrix*b,axis=1)
    return (float(a.mean()/b.mean()),np.percentile(vals,[2.5,97.5]).tolist())

def analyze(directory,manifest,data,lock,development):
    verified=load(directory/"VERIFY.json")
    plan,groups,arms,scores,status,wall=replay_cube(directory)
    x,families,feature_us=inputs(manifest,groups)
    m=load(manifest)
    assert list(lock["features"])==list(FEATURES)
    n,a,r=scores.shape
    h=r//2
    assert n==verified["programs"] and r==REPEATS and h*2==r
    ids={a:i for i,a in enumerate(arms)}
    # Do not adapt the deployed policy to test telemetry. Retain failed selections
    # with their full PAR2 penalty. Oracle is diagnostic over the locked menu.
    names=lock["eligible"]
    subset=np.array([ids[a] for a in names])
    glob=ids[data["global_arm"]]
    si={a:i for i,a in enumerate(names)}
    if development:
        np.testing.assert_array_equal(groups,lock["cv_groups"])
        selected=subset[lock["cv_chosen"]]
        secondary=selected.copy()
    else:
        pred=predict(lock["fit"],x)
        selected=subset[choose(pred,si[data["global_arm"]],lock["gate_s"])]
        secondary=subset[choose(pred,si[data["global_arm"]],0)]
    if lock["backoff_to_global"]:
        selected=np.full(n,glob)
    i=np.arange(n)
    first=scores[:,:,:h].mean(axis=2)
    second=scores[:,:,h:].mean(axis=2)
    first_choice=subset[np.argmin(first[:,subset],axis=1)]
    second_choice=subset[np.argmin(second[:,subset],axis=1)]
    cross=(first[i,second_choice]+second[i,first_choice])/2
    same=(first[i,first_choice]+second[i,second_choice])/2
    empirical=scores.mean(axis=2)
    variants={"default":empirical[:,ids["default"]],"default_alias":empirical[:,ids["default_alias"]],
              "global":empirical[:,glob],"selector":empirical[i,selected],
              "selector_secondary":empirical[i,secondary],"oracle_same_half":same,"oracle_disjoint":cross}
    rng=np.random.default_rng(129418)
    mat=group_draws(families,rng)
    arms_chosen={"default":np.full(n,ids["default"]),"default_alias":np.full(n,ids["default_alias"]),
                 "global":np.full(n,glob),"selector":selected,"selector_secondary":secondary}
    known_effects={k:{"par2":float(v.mean()),"ratio_global":frac(v,variants["global"],mat),
               "ratio_default":frac(v,variants["default"],mat)} for k,v in variants.items()}
    for label,idx in arms_chosen.items():
        known_effects[label]["counts"]=dict(Counter(status[i,idx].flatten()))
        known_effects[label]["arms"]=dict(Counter(arms[j] for j in idx))
    for label,j1,j2 in [("oracle_same_half",first_choice,second_choice),("oracle_disjoint",second_choice,first_choice)]:
        known_effects[label]["counts"]=dict(Counter(np.concatenate([status[i,j1,:h],status[i,j2,h:]],axis=1).flatten()))
    a0, a1=ids["default"],ids["default_alias"]
    both=(status[:,a0]=="solved")&(status[:,a1]=="solved")
    alias=np.abs(wall[:,a0]-wall[:,a1])[both]
    noise={"n_both_solved":int(alias.size),"median_abs_ms":float(np.median(alias)*1000),"p90_abs_ms":float(np.percentile(alias,90)*1000),
           "different_timeout":int(np.sum((status[:,a0]=="timeout")!=(status[:,a1]=="timeout")))}
    corrected=float(1-cross.mean()/variants["global"].mean())
    optimism=frac(cross,same,mat)
    excess=cross-same
    result={"programs":n,"families":len(set(families)),"family_members":dict(Counter(families)),"repeats":r,
            "checks_per_pass":sum(len(row["oracle"]) for row in m["sessions"]),"rejected":len(m["rejected"]),
            "reject_reasons":dict(Counter(row["reason"] for row in m["rejected"])),
            "effects":known_effects,"noise":noise,"feature_us":feature_us,"source_scope":"full Java source available before execution",
            "disjoint_oracle_headroom":corrected,"optimism_ratio":optimism,
            "optimism_par2_s":float(excess.mean()),"oracle_choice_agreement":float(np.mean(first_choice==second_choice)),
            "bootstrap":{"iterations":B,"seed":129418,"unit":"whole generator family, paired arms"},
            "per_program":{k:{"family":families[j],"features":x[j].tolist(),
                "selector_arm":arms[selected[j]],"same_half_choices":[arms[first_choice[j]],arms[second_choice[j]]],
                "par2":{v:float(matrix[j]) for v,matrix in variants.items()},
                "per_arm":{arm:{"par2":float(empirical[j,ids[arm]]),"counts":dict(Counter(status[j,ids[arm],:]))} for arm in arms}}
                for j,k in enumerate(groups)}}
    write_json(directory/"metrics.json",result)
    return result

def portfolio_card(result,directory,manifest,data,lock,development):
    out="02-dev" if development else "03-portfolio"
    base=directory.relative_to(ROOT)
    n,f,r=result["programs"],result["families"],result["repeats"]
    results=result["effects"]
    total=Counter()
    for c in load(directory/"VERIFY.json")["counts"].values():
        total.update(c)
    c={k:results[k] for k in ["default","global","selector","oracle_same_half","oracle_disjoint"]}
    labels={"default":"default","default_alias":"default (дубль)","global":f'Глобальный {data["global_arm"]}',
            "selector":"Зафиксированный селектор","selector_secondary":"Ridge, дополнительный анализ",
            "oracle_same_half":"Оракул на повторах выбора","oracle_disjoint":"Оракул на независимых повторах"}
    stage_text=("Новая разработочная выборка. Прогнозы селектора строились при исключении целого семейства. Гиперпараметры выбраны по этим же CV-оценкам, поэтому сравнение остаётся разведочным."
                if development else "Новый закрытый набор. Манифест создан после фиксации меню, модели, порога и основного сравнения. Семейства исключены из всех ранее использованных наборов.")
    texts=[
        f"{stage_text} {n} Java-программ в {f} семействах, 1 полный сеанс на программу, {result['checks_per_pass']} проверок за обход, {r} повторов на ячейку. Отклонено {result['rejected']} трасс: неполнота, неизвестные ответы, повторы или независимый аудит. Явные причины сохранены в манифесте.",
        f"Меню из {len(data['arms'])-1} конфигураций и дубля default зафиксировано по исходным 11 программам до генерации. Глобальный {data['global_arm']} выбран только там. Запрещён ite-simp=on, каждую настройку проверили на тренировочных сеансах с моделями и unsat cores.",
        f"Надежда портфеля измерена двумя половинами по {r//2} повтора. Выбор лучшего для каждой программы на первой половине оценивается на второй, затем роли меняются. На повторах выбора значения оптимистичнее на {1000*result['optimism_par2_s']:+.1f} мс. Выборы двух половин совпали у {pct(result['oracle_choice_agreement'])} программ.",
        f"Глобальный режим {c['global']['par2']:.3f} с. Оракул на отдельных повторах {c['oracle_disjoint']['par2']:.3f} с, запас {pct(result['disjoint_oracle_headroom'])}, отношение {fmt_ci(c['oracle_disjoint']['ratio_global'])}. Оракул использует ретроспективный выбор. Интервалы получены парным bootstrap по {f} целым семействам, {B} выборок.",
        f"Ridge использует только девять признаков Java-исходника, доступных до запуска. Извлечение в этой серии {result['feature_us']:.0f} мкс на файл в среднем. Лучший разработочный режим в CV имел PAR2 {lock['cv_best_par2']:.3f} с против {lock['cv_global_par2']:.3f} с у глобального. {'Основное правило вернулось к глобальному по заложенному порогу.' if lock['backoff_to_global'] else 'Основное правило включает порог ожидаемого выигрыша на Ridge.'}",
        f"Дубль default: медиана абсолютной разницы {result['noise']['median_abs_ms']:.1f} мс, P90 {result['noise']['p90_abs_ms']:.1f} мс на {result['noise']['n_both_solved']} полных парах. Таймаут разошёлся в {result['noise']['different_timeout']} из {n*r} пар. Среди {sum(total.values())} запусков меню зарегистрированы {total['timeout']} таймаутов, {total['unknown']} unknown, {total['error']} ошибок и {total['incorrect']} несовпадений."
    ]
    tables=[card_table(f"Сравнение на {'разработке' if development else 'закрытом тесте'} · {n} программ, {f} семейств, {n*r} запусков на строку",
              ["Режим","PAR2, с","К глобальному [95% ДИ]","К default [95% ДИ]","Таймауты","Ошибки + unknown + несовпадения"],
              [[labels[k],f'{v["par2"]:.3f}',fmt_ci(v["ratio_global"]),fmt_ci(v["ratio_default"]),
                v["counts"].get("timeout",0),sum(v["counts"].get(z,0) for z in ("error","unknown","incorrect"))]
               for k,v in results.items()])]
    summary=(f"{'Новая разработка' if development else 'Закрытый тест портфеля'}: {n} программ, "
             f"PAR2 {c['default']['par2']:.3f} с у default, {c['global']['par2']:.3f} с у глобальной "
             f"настройки, {c['selector']['par2']:.3f} с у селектора и {c['oracle_disjoint']['par2']:.3f} с у оракула на отдельных повторах.")
    write_json(ROOT/f"evidence/{out}.json",{
        "title":"02 · Свежая разработочная выборка" if development else "03 · Закрытый тест портфеля",
        "done":f"Проверены {n} новых программ из {f} независимых семейств на {'разработке' if development else 'закрытом тесте'} портфеля.",
        "summary":summary,"paragraphs":texts,"tables":tables,
        "paths":["protocols/portfolio-menu.json","protocols/portfolio-selector.json",str(base/"plan.json"),
                 str(base/"worker-{0..5}.jsonl"),str(base/"metrics.json"),str(base/"VERIFY.json"),
                 os.path.relpath(manifest,ROOT),os.path.relpath(manifest.parent/"z3-audit.jsonl",ROOT)],
        "recommendation":"Опираться на закрытый интервал для решения о смене default. Запас оракула служит ориентиром для следующих признаков и проверки переносимости.",
        "next":("Проверить зафиксированные решения на отдельных семействах." if development else
                "Проверить те же настройки на свежих полных сеансах библиотечного Java-кода и сравнить поиск Sol с RF при одинаковой истории и явном бюджете.")
    })
    build()

def run(name):
    data=menu()
    if name=="dev":
        split="portfolio-dev"
        gate=MENU
        prev=[]
    else:
        split="portfolio-test"
        gate=LOCK
        prev=[BASE/"dev-corpus"]
    target=BASE/f"{name}-corpus"
    if (target/"manifest.json").exists():
        manifest=target/"manifest.json"
        assert (target/"release.json").exists()
        assert load(target/"release.json")["decision_lock_sha256"]==digest(gate)
    else:
        manifest=materialize(split,gate,target,prev)
    directory=BASE/f"{name}-replay"
    if not (directory/"VERIFY.json").exists():
        launch(directory,manifest,data["arms"],REPEATS,943691 if name=="dev" else 314630,1800)
    lock=fold_lock(directory,manifest,data) if name=="dev" else load(LOCK)
    result=analyze(directory,manifest,data,lock,name=="dev")
    portfolio_card(result,directory,manifest,data,lock,name=="dev")
    return result

if __name__=="__main__":
    stage=sys.argv[1]
    print("menu frozen" if stage=="menu" and menu() else run(stage)["programs"])
