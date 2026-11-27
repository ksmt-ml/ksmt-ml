"""Retime shared warm and later finalists in paired order after a flat online result."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from corpus import materialize
from replay import CORES, DATA, ROOT, digest, launch, lines, load, now
from report import build, write_json
from search import ARMS, BASE as SEARCH, HORIZON, MATCHED_COUNT, SEEDS, canonical
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE=ROOT/"runs/14b-search-retiming"
SCOPE=ROOT/"protocols/search-retiming-scope.json"
SLATES=ROOT/"protocols/search-retiming-slates.json"
LOCK=BASE/"confirm-lock.json"
R=8

def selections():
    scope=load(SCOPE)
    assert scope["pre_primary_holdout_release"]
    assert digest(SEARCH/"validation-shortlists.json")==scope["original_shortlists_sha256"]
    if SLATES.exists():
        return load(SLATES)
    chosen={}
    for s in SEEDS:
        base=SEARCH/f"seed-{s}"
        warm=lines(base/"warm/candidates.jsonl")
        menus={"warm":sorted([r for r in warm if r["eligible"]],key=lambda r:r["par2"])[:3],
               "late":[]}
        exact={"default":{}}
        keyed={canonical({}):"default"}
        menus["warm"].append(warm[0])
        origins={"warm":[],"late":[]}
        for method in ARMS:
            measured=[r for r in lines(base/method/"candidates.jsonl")
                      if r["complete"] and r["eligible"] and r["finish_s"]<=HORIZON]
            tails=sorted(measured,key=lambda r:(r["par2"],r["candidate_id"]))[:2]
            early=[r for r in measured if r["candidate_id"]<MATCHED_COUNT]
            tails.extend(sorted(early,key=lambda r:(r["par2"],r["candidate_id"]))[:1])
            menus["late"].extend({**r,"method":method} for r in tails)
        for provenance,items in menus.items():
            for measurement in items:
                c=measurement["config"]
                key=canonical(c)
                if key not in keyed:
                    name=f"T{len(exact):02d}"
                    keyed[key]=name
                    exact[name]=c
                n=keyed[key]
                origins[provenance].append({"label":n,"historical_candidate":measurement["candidate_id"],
                                            "historical_train_par2":measurement["par2"],"source_arm":measurement.get("method"),
                                            "historical_finish_s":measurement["finish_s"]})
        chosen[str(s)]={"configs":exact,"origins":origins}
    data={"locked":now(),"scope_sha256":digest(SCOPE),"source_train_complete_sha256":digest(SEARCH/"train-complete.json"),
          "seeds":chosen,"repeats":R}
    write_json(SLATES,data)
    SLATES.chmod(0o444)
    return data

def index_origins(slate,arm):
    return list(dict.fromkeys(q["label"] for q in slate["origins"][arm]))

def run_seed(base,s,core,slate):
    if not (base/"VERIFY.json").exists():
        launch(base,DATA/"train/manifest.json",slate["configs"],R,121281+s,1850,cores=(core,))
    return load(base/"VERIFY.json")

def measure():
    data=selections()
    BASE.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=len(SEEDS)) as tasks:
        full={s:tasks.submit(run_seed,BASE/f"seed-{s}",s,core,data["seeds"][str(s)])
              for s,core in zip(SEEDS,CORES)}
        for job in full.values():
            job.result()
    results={}
    score_pairs=[]
    groups_set=[]
    for s in SEEDS:
        path=BASE/f"seed-{s}"
        plan,groups,arms,scores,status,wall=replay_cube(path)
        source=data["seeds"][str(s)]
        groups_set.append(groups)
        eligible=~np.isin(status,["error","incorrect","unknown"]).any(axis=(0,2))
        a={phase:[arms.index(k) for k in index_origins(source,phase) if eligible[arms.index(k)]]
           for phase in ("warm","late")}
        assert all(a.values())
        parts=[scores[:,:,:R//2].mean(axis=2),scores[:,:,R//2:].mean(axis=2)]
        frame={"candidates":len(arms),"first_half_chooses_second_half_scores":{},
               "initial_config_mean_calibration":{}}
        for role,ix in a.items():
            arr=np.array(ix)
            front=arr[np.argmin(parts[0][:,ix].mean(axis=0))]
            back=arr[np.argmin(parts[1][:,ix].mean(axis=0))]
            independent=(parts[0][:,back]+parts[1][:,front])/2
            frame["first_half_chooses_second_half_scores"][role]={
                "first":arms[front],"last":arms[back],"par2":float(independent.mean()),"by_program":independent.tolist()}
            old={p["label"]:p["historical_train_par2"] for p in source["origins"][role]}
            frame["initial_config_mean_calibration"][role]=float(np.mean(
                [scores[:,i,:].mean()-old[arms[i]] for i in ix]))
        a0=frame["first_half_chooses_second_half_scores"]["warm"]
        a1=frame["first_half_chooses_second_half_scores"]["late"]
        frame["late_to_warm"]=a1["par2"]/a0["par2"]
        frame["independently_verified_replays"]=load(path/"VERIFY.json")["rows"]
        score_pairs.append((a0["by_program"],a1["by_program"]))
        results[str(s)]=frame
    assert all(g==groups_set[0] for g in groups_set)
    costs=np.array(score_pairs)
    rng=np.random.default_rng(911397)
    I=rng.integers(len(SEEDS),size=(B,len(SEEDS)))
    J=rng.integers(len(groups_set[0]),size=(B,len(groups_set[0])))
    warm,late=(costs[:,k,:] for k in range(2))
    measured=late.mean()/warm.mean()
    ratio= np.sum(late[I[:,:,None],J[:,None,:]],axis=(1,2))/np.sum(warm[I[:,:,None],J[:,None,:]],axis=(1,2))
    passed=sum(r["late_to_warm"]<=.99 for r in results.values())>=2
    result={"seeds":list(SEEDS),"training_programs":len(groups_set[0]),"repeats":R,
            "primary_latency_result":[float(measured),np.percentile(ratio,[2.5,97.5]).tolist()],
            "seed_analysis":results,"confirmatory_gate_passed":passed,"groups":groups_set[0]}
    write_json(BASE/"metrics.json",result)
    notice=f"При общем повторном замере поздние настройки дали отношение {fmt_ci(result['primary_latency_result'])} к стартовым на 15 обучающих программах."
    write_json(ROOT/"evidence/15b-search-retiming.json",{
        "title":"15б · Старт против поздних предложений при общем расписании",
        "done":"Проверено влияние общего рандомизированного повторного замера на отбор поздних настроек.",
        "summary":notice,
        "paragraphs":[
            "Во всех четырёх seed прежние финалисты пришли из общего замера, сделанного до запуска шести основных воркеров. Поэтому сравнили эти настройки с лучшими измеренными поздними предложениями при одинаковых условиях внутри программы и повтора. Список кандидатов зависит только от исходного обучения.",
            f"{len(groups_set[0])} одних и тех же обучающих программ, {R} повторов на ячейку, четыре seed поиска. Из каждой половины выбирали лучший вариант, другую половину использовали для оценки. Отражены неопределённость целых программ и seed и разница со старыми одиночными замерами. Сравнение между эпохами само по себе не устанавливает причину сдвига.",
            ("Поздние настройки улучшили независимый повторный результат минимум на 1% не менее чем в двух seed. Действует заранее заданная проверка на отдельном корпусе." if passed else
             "Поздние настройки не показали заранее заданного выигрыша минимум в двух seed. Дополнительный закрытый корпус по этому контрасту не открывается.")
        ],
        "tables":[card_table("Те же 15 обучающих программ · 8 повторов на настройку",["Seed","Векторов","PAR2 старт, с","PAR2 поздние, с",
                   "Поздние / старт","Сдвиг старта к прежнему, мс","Сдвиг поздних к прежнему, мс"],
                   [[s,v["candidates"],f'{v["first_half_chooses_second_half_scores"]["warm"]["par2"]:.3f}',
                     f'{v["first_half_chooses_second_half_scores"]["late"]["par2"]:.3f}',f'{v["late_to_warm"]:.3f}',
                     f'{1000*v["initial_config_mean_calibration"]["warm"]:+.1f}',
                     f'{1000*v["initial_config_mean_calibration"]["late"]:+.1f}'] for s,v in results.items()])],
        "paths":["protocols/search-retiming-scope.json","protocols/search-retiming-slates.json",
                 "runs/14b-search-retiming/seed-<seed>/VERIFY.json","runs/14b-search-retiming/metrics.json"],
        "recommendation":"Измерять общий старт и поздние кандидаты при одинаковых условиях или перепроверять отбор в случайном парном порядке.",
        "next":"При подтверждённом выигрыше поздних настроек открыть новый независимый набор после одинаковой валидации."
    })
    build()
    return result

def candidate_validation(s,core,slate):
    original=BASE/f"seed-{s}"
    _,groups,arms,score,st,_=replay_cube(original)
    means={n:float(score[:,i,:].mean()) for i,n in enumerate(arms)}
    vectors={"default":{}}
    labels={}
    for k in ("warm","late"):
        top=sorted(index_origins(slate,k),key=lambda n:(means[n],n))[:2]
        labels[k]=top
        for arm in top:
            vectors[arm]=slate["configs"][arm]
    out=original/"validation"
    if not (out/"VERIFY.json").exists():
        launch(out,DATA/"validation/manifest.json",vectors,4,s+89343,760,cores=(core,))
    v=load(out/"VERIFY.json")
    selected={"default":{}}
    for name,opts in labels.items():
        elig=[p for p in opts if not any(v["counts"][p].get(w,0) for w in ("error","incorrect","unknown"))]
        pick=min(elig,key=lambda x:(v["par2"][x],x))
        selected[name]=vectors[pick]
    return {"configs":selected,"validation_sha256":digest(out/"VERIFY.json")}

def test_retest():
    r=load(BASE/"metrics.json")
    if not r["confirmatory_gate_passed"]:
        return
    slates=load(SLATES)["seeds"]
    if not LOCK.exists():
        with ThreadPoolExecutor(max_workers=len(SEEDS)) as worker:
            vals={s:worker.submit(candidate_validation,s,core,slates[str(s)]) for s,core in zip(SEEDS,CORES)}
            data={s:f.result() for s,f in vals.items()}
        write_json(LOCK,{"locked":now(),"retiming_sha256":digest(BASE/"metrics.json"),"scope_sha256":digest(SCOPE),
                         "selections":data})
        LOCK.chmod(0o444)
    conf=load(LOCK)
    corpus=BASE/"heldout"
    manifest=corpus/"manifest.json"
    if not manifest.exists():
        priors=sorted({p.parent for p in (ROOT/"runs").glob("**/programs.json") if corpus not in p.parents})
        manifest=materialize("search-retest-test",LOCK,corpus,priors,core=CORES[0])
    assert load(corpus/"release.json")["decision_lock_sha256"]==digest(LOCK)
    with ThreadPoolExecutor(max_workers=len(SEEDS)) as worker:
        tasks=[]
        for s,core in zip(SEEDS,CORES):
            dest=BASE/f"test-{s}"
            if not (dest/"VERIFY.json").exists():
                tasks.append(worker.submit(launch,dest,manifest,conf["selections"][str(s)]["configs"],6,s+60223,850,(core,)))
        for t in tasks:
            t.result()
    scores=[]
    rows=[]
    groups=[]
    for s in SEEDS:
        out=BASE/f"test-{s}"
        v=load(out/"VERIFY.json")
        _,groups,arms,cube,st,wall=replay_cube(out)
        scores.append(np.stack([cube[:,arms.index(k)].mean(axis=1) for k in ("warm","late","default")]))
        for key in ("warm","late","default"):
            q=v["counts"][key]
            rows.append([s,key,f'{v["par2"][key]:.3f}',q.get("timeout",0),q.get("error",0),q.get("incorrect",0)])
    arr=np.asarray(scores)
    fam={r["group"]:r["family"] for r in load(manifest)["sessions"]}
    from portfolio import group_draws
    rng=np.random.default_rng(85600)
    draw=group_draws([fam[g] for g in groups],rng)
    m=rng.multinomial(len(SEEDS),np.full(len(SEEDS),1/len(SEEDS)),size=B)
    p=np.sum((draw@arr[:,1,:].T)*m,axis=1)/np.sum((draw@arr[:,0,:].T)*m,axis=1)
    tv={"programs":len(groups),"families":len(set(fam.values())),"repeats":6,"seeds":list(SEEDS),
        "late_to_warm":[float(arr[:,1,:].mean()/arr[:,0,:].mean()),np.percentile(p,[2.5,97.5]).tolist()],
        "rows":rows}
    write_json(BASE/"confirm-metrics.json",tv)
    summary=f"На отдельных {len(groups)} программах поздние настройки после повторного отбора дали {fmt_ci(tv['late_to_warm'])} к стартовым."
    write_json(ROOT/"evidence/16a-search-retime-confirm.json",{
        "title":"16а · Закрытая проверка повторно отобранных настроек",
        "done":"Поздние и стартовые варианты после одинаковой переоценки проверены на новом наборе.",
        "summary":summary,
        "paragraphs":[f"Новый тест из {len(groups)} программ, {len(set(fam.values()))} семейств, 6 повторов и 4 seed. Порог выигрыша на разработке определён заранее. Настройки выбраны по исходному обучению и отдельной парной первичной валидации до открытия seed теста.",
                      "Группировка пересэмплирует целые семейства и seed. Этот результат проверяет другой порядок переоценки. Основное сравнение замкнутых оптимизаторов имеет собственные зафиксированные настройки."],
        "tables":[card_table(f"По {len(groups)*6} замеров на строку",["Seed","Режим","PAR2, с","Таймауты","Ошибки","Несовпадения"],rows)],
        "paths":["runs/14b-search-retiming/confirm-lock.json","runs/14b-search-retiming/confirm-metrics.json",
                 "runs/14b-search-retiming/heldout/manifest.json","runs/14b-search-retiming/test-<seed>/VERIFY.json"],
        "recommendation":"Фиксировать совместную повторную оценку стартовых и поздних настроек в протоколе отбора.",
        "next":"Проверить этот порядок после замены нагрузочного набора на целевые библиотечные методы."
    })
    build()
if __name__=="__main__":
    measure()
