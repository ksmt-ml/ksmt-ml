"""Analyse fixed online snapshots, select on old validation, then release fresh test."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import numpy as np

from corpus import materialize
from portfolio import group_draws
from replay import CORES, ROOT, digest, launch, lines, load, now
from report import build, write_json
from search import ARMS, BASE, HORIZON, MATCHED_COUNT, PLAN, SEEDS, SMOKE, VAL, canonical, order, vector
from stability_metrics import B, card_table, fmt_ci, replay_cube

VALIDATION_REPEATS=6
TEST_REPEATS=6

def records(seed,arm):
    base=BASE/f"seed-{seed}"
    warm=lines(base/"warm/candidates.jsonl")
    extra=lines(base/arm/"candidates.jsonl")
    return warm,extra

def audit_decisions(seed,arm):
    base=BASE/f"seed-{seed}"
    f=base/arm
    stored=load(base/"proposals.json")
    warm,items=records(seed,arm)
    timeline=warm+items
    complete_before=list(warm)
    decisions=lines(f/"decisions.jsonl")
    if arm=="sol":
        telemetry=lines(f/"model-calls.jsonl")
        valid=0
    for d in decisions:
        turn=d["round"]
        chunk=stored["rounds"][turn]
        assert chunk["forced"]==d["forced"]
        predicted=d["predictions"]
        assert d["selection"]==(chunk["forced"] if predicted is None else order(predicted,chunk["forced"],8))
        if arm=="sol":
            prompt_path=f/"model"/f"call-{turn:03d}.prompt"
            request=json.loads(prompt_path.read_text().splitlines()[-1])
            c=next(r for r in telemetry if r["round"]==turn)
            assert request["new_real_feedback_ids"]==d["feedback"]==c["new_real_feedback_ids"]
            assert request["elapsed_search_seconds"]==round(c["elapsed_search_seconds"],3)
            assert request["remaining_search_seconds"]==round(c["remaining_search_seconds"],3)
            assert request["candidates"]==[vector(p) for p in chunk["pool"]]
            assert [r["id"] for r in request["history"]]==[r["candidate_id"] for r in complete_before]
            for sent,r in zip(request["history"],complete_before,strict=True):
                assert sent["x"]==vector(r["config"]) and sent["cost"]==round(r["score"],6)
            if c["valid_representation"]:
                valid+=1
                assert c["turn_completed"] and c["rc"]==0
                assert predicted==load(f/"model/work"/f"call-{turn:03d}.reply.json")["predictions"]
        feedback=[r for r in items if r["proposal"]["round"]==turn]
        assert [r["proposal"]["index"] for r in feedback]==d["selection"][:len(feedback)]
        assert all(r["config"]==chunk["pool"][r["proposal"]["index"]] for r in feedback)
        complete_before.extend(r for r in feedback if r["complete"])
    result={"verifies":["decision order","fixed pool","forced exploration","picked configs"],
            "decision_rounds":len(decisions),"verified_measurements":len(complete_before)}
    if arm=="sol":
        result.update({"valid_model_calls":valid,"all_model_calls":len(telemetry),
                       "failed_model_calls":sum(not r["valid_representation"] for r in telemetry),
                       "scoring_calls_using_tools":sum(r["tool_commands"]>0 and r["valid_representation"] for r in telemetry),
                       "model_callable_wall_s":sum(r["wall_s"] for r in telemetry),
                       "valid_feedback_calls":sum(r["valid_representation"] and any(i>=len(warm) for i in r["new_real_feedback_ids"]) for r in telemetry),
                       "usage":dict(sum((Counter(r["usage"]) for r in telemetry),Counter()))})
        assert valid==sum(r["valid_representation"] for r in telemetry)
        result["verifies"]+=["actual model event","feedback matches measured training","time and remaining budget"]
    write_json(f/"feedback-VERIFY.json",result)
    return result

def publish_progress(statuses):
    rows=[]
    full_counts=Counter()
    missed=0
    deviations=0
    for seed in statuses:
        for arm,rec in statuses[seed].items():
            count=rec["verified"]["counts"]
            full_counts.update(count)
            if arm in ("sol","rf"):
                extra=audit_decisions(int(seed),arm)
            else:
                extra={}
            missed+=extra.get("failed_model_calls",0)
            deviations+=extra.get("scoring_calls_using_tools",0)
            rows.append([seed,arm,rec["completed"],rec["eligible"],f'{rec["logical_wall_s"]:.1f}',
                         f'{rec["best_train"]:.3f}',count.get("timeout",0),count.get("error",0),
                         count.get("incorrect",0),extra.get("valid_model_calls","—"),extra.get("valid_feedback_calls","—")])
    diagnosis=load(SMOKE/"VERIFY.json")
    summary=f"Проверены результаты замкнутого поиска для {len(statuses)} seed. Каждый метод получил реальную обратную связь на 15 обучающих программах."
    write_json(ROOT/"evidence/05-search-progress.json",{
        "title":"05 · Замкнутый поиск, проверка обучения",
        "done":f"Независимо сверены обучение и обратная связь Sol/RF/SMAC на {len(statuses)} seed.",
        "summary":summary,
        "paragraphs":[
            "Sol и RF получают одинаковый размеченный старт и оценивают один пул предложений на раунд. Две случайные точки и правило выбора восьми ближайших по прогнозу общие. После каждого раунда Sol получает измеренные значения, фактическое время и оставшийся бюджет. Native SMAC получает ту же стартовую историю и предлагает варианты через ask/tell.",
            f"Для каждого seed 15 независимых обучающих программ, 1 повтор на конфигурацию. Указанное число конфигураций включает общий старт из 14 реальных замеров, который целиком учтён во времени каждого метода. Остальные замеры раздельны. Horizon {HORIZON} с, один закреплённый физический CPU на метод; ограничения solver 1,50 с и до 85 с на модельное обращение.",
            f"Изоляция и проба всех трёх методов прошли. Повторная проверка сопоставила зафиксированные пулы, прогнозы Sol, раунды, реальную обратную связь и измеренные исходы. В рабочих обращениях Sol {missed} сбоев или таймаутов и {deviations} валидных ответов с вызовом инструмента. Ошибки обучения остаются в таблице. Эта таблица описывает донастройку на исходном обучении, обобщение измеряется отдельно."
        ],
        "tables":[card_table(f"Поиск на исходном обучении · {len(statuses)} seed, 15 программ, 1 повтор на конфигурацию",
                  ["Seed","Метод","Завершено","Допущено","Фактическое время, с","Лучший PAR2 train, с",
                   "Таймауты после старта","Ошибки после старта","Несовпадения","Валидные ответы Sol","Ответы Sol с новой обратной связью"],rows)],
        "paths":["protocols/search.json","runs/05-search/smoke/VERIFY.json","runs/05-search/progress.json",
                 "runs/05-search/seed-<seed>/warm/candidates.jsonl",
                 "runs/05-search/seed-<seed>/<метод>/feedback-VERIFY.json",
                 "runs/05-search/seed-<seed>/<метод>/sessions.jsonl"],
        "recommendation":"Сопоставлять поисковые методы по закрытой оценке результата и учитывать время работы модели.",
        "next":"Повторить валидацию финалистов для двух срезов бюджета и зафиксировать настройки до генерации независимого теста."
    })
    build()

def shortlist(seed,all_seeds):
    histories={}
    for arm in ARMS:
        warm,extra=records(seed,arm)
        data=[r for r in warm+extra if r["complete"] and r["finish_s"]<=HORIZON]
        histories[arm]=data
    cap=min(MATCHED_COUNT,*[len(v) for v in histories.values()])
    slate={"default":{}}
    reverse={canonical({}):"default"}
    selections={}
    for arm,data in histories.items():
        for mode,sequence in (("wall",data),("count",data[:cap])):
            top=sorted([r for r in sequence if r["eligible"]],
                       key=lambda r:(r["par2"],r["candidate_id"]))[:3]
            labels=[]
            for entry in top:
                cfg=entry["config"]
                key=canonical(cfg)
                if key not in reverse:
                    name=f"T{len(slate):02d}"
                    slate[name]=cfg
                    reverse[key]=name
                labels.append({"candidate":entry["candidate_id"],"arm_id":reverse[key],
                               "train_par2":entry["par2"],"completed_at":entry["finish_s"]})
            if not any(i["arm_id"]=="default" for i in labels):
                labels.append({"candidate":0,"arm_id":"default","train_par2":data[0]["par2"],
                               "completed_at":data[0]["finish_s"]})
            selections[f"{arm}-{mode}"]=labels
    return {"seed":seed,"fixed_count":cap,"count_target":MATCHED_COUNT,
            "n_evaluations":{k:len(v) for k,v in histories.items()},
            "configs":slate,"shortlist":selections}

def validation_replay(seed,core,slate):
    base=BASE/f"seed-{seed}"
    path=base/"validation"
    if (base/"validation-lock.json").exists():
        previous=load(base/"validation-lock.json")
        assert previous["validation_sha256"]==digest(path/"VERIFY.json")
        assert all(previous[key]==value for key,value in slate.items())
        return previous
    if not (path/"VERIFY.json").exists():
        launch(path,VAL,slate["configs"],VALIDATION_REPEATS,seed+541514,1900,cores=(core,))
    v=load(path/"VERIFY.json")
    chosen={}
    for key,entries in slate["shortlist"].items():
        eligible=[e for e in entries if not any(v["counts"][e["arm_id"]].get(s,0) for s in ("error","incorrect","unknown"))]
        assert eligible
        best=min(eligible,key=lambda e:(v["par2"][e["arm_id"]],e["candidate"]))
        cfg=slate["configs"][best["arm_id"]]
        chosen[key]={**best,"config":cfg,"val_par2":v["par2"][best["arm_id"]],
                     "val_counts":v["counts"][best["arm_id"]]}
    r={**slate,"selected":chosen,"locked":now(),"validation_sha256":digest(path/"VERIFY.json")}
    write_json(base/"validation-lock.json",r)
    return r

def test_replay(base,seed,core,manifest,locked):
    settings={"default":{},"default_alias":{}}
    distinct={canonical({}):"default"}
    label_to_actual={"default":"default","default_alias":"default_alias"}
    for label,selection in locked["selected"].items():
        c=selection["config"]
        key=canonical(c)
        if key not in distinct:
            distinct[key]=label
            settings[label]=c
        label_to_actual[label]=distinct[key]
    out=base/f"seed-{seed}"
    if not (out/"VERIFY.json").exists():
        launch(out,manifest,settings,TEST_REPEATS,seed+653566,1800,cores=(core,))
    write_json(out/"mapping.json",label_to_actual)
    return out

def verify_test_lock(manifest,lock_file,locks):
    directory=manifest.parent
    release=load(directory/"release.json")
    assert release["decision_lock_sha256"]==digest(lock_file)
    assert load(lock_file)["locks"]=={str(k):v for k,v in locks.items()}
    assert load(lock_file)["locked"]<=release["unlocked"]
    for seed in locks:
        assert locks[seed]==load(BASE/f"seed-{seed}"/"validation-lock.json")

def tested(folder, manifest, rd, locks):
    results={}
    for seed in locks:
        directory=folder/f"seed-{seed}"
        mi=load(directory/"mapping.json")
        plan,groups,arms,scores,status,wall=replay_cube(directory)
        assert plan["repeats"]==TEST_REPEATS
        for name,choice in locks[seed]["selected"].items():
            assert choice["config"]==plan["configs"][mi[name]]
        values={label:scores[:,arms.index(source),:].mean(axis=1) for label,source in mi.items()}
        stats={label:Counter(status[:,arms.index(source),:].reshape(-1)) for label,source in mi.items()}
        a0,a1=arms.index("default"),arms.index("default_alias")
        paired=(status[:,a0]=="solved")&(status[:,a1]=="solved")
        noise=np.abs(wall[:,a0]-wall[:,a1])[paired]
        flips=int(np.sum((status[:,a0]=="timeout")!=(status[:,a1]=="timeout")))
        results[seed]={"groups":groups,"values":values,"stats":stats,"noise":noise,"flips":flips}
    telemetry={seed:load(BASE/f"seed-{seed}/sol/feedback-VERIFY.json") for seed in results}
    viable=[seed for seed in results if telemetry[seed]["valid_model_calls"]>=2 and
            telemetry[seed]["valid_feedback_calls"]>=1 and telemetry[seed]["scoring_calls_using_tools"]==0]
    if not viable:
        raise RuntimeError("No true online Sol seed for confirmed comparison")
    labels=list(results[viable[0]]["values"])
    groups=results[viable[0]]["groups"]
    assert all(results[s]["groups"]==groups for s in results)
    m=load(manifest)
    group_by={r["group"]:r for r in m["sessions"]}
    families=[group_by[x]["family"] for x in groups]
    n,s,r=len(groups),len(viable),TEST_REPEATS
    rng=np.random.default_rng(820202)
    draws=group_draws(families,rng)
    seed_mult=rng.multinomial(s,np.full(s,1/s),size=B)
    all_stats={}
    effects={}
    by_label={name:np.stack([results[v]["values"][name] for v in viable],axis=0) for name in labels}

    def paired(a,b):
        aa,bb=by_label[a],by_label[b]
        def weighted(multiplicity):
            return np.sum(multiplicity*(draws@aa.T),axis=1)/np.sum(multiplicity*(draws@bb.T),axis=1)
        group=weighted(1)
        both=weighted(seed_mult)
        return {"ratio":float(aa.mean()/bb.mean()),"family_95":np.percentile(group,[2.5,97.5]).tolist(),
                "family_seed_95":np.percentile(both,[2.5,97.5]).tolist(),
                "per_seed":{seed:float(aa[i].mean()/bb[i].mean()) for i,seed in enumerate(viable)}}
    for label in labels:
        all_stats[label]={"par2":float(by_label[label].mean()),"to_default":paired(label,"default"),
                          "counts":dict(sum((results[v]["stats"][label] for v in viable),Counter()))}
    for mode in ("wall","count"):
        for reference in ("rf","smac"):
            a,b=f"sol-{mode}",f"{reference}-{mode}"
            effects[f"{a}/{b}"]=paired(a,b)
    values={str(seed):{label:[float(x) for x in vals] for label,vals in data["values"].items()}
                 for seed,data in results.items()}
    noises=np.concatenate([results[seed]["noise"] for seed in viable])
    result={"programs":n,"families":len(set(families)),"search_seeds":viable,"all_seeds":list(results),
            "excluded_from_primary":[seed for seed in results if seed not in viable],
            "viable_incomplete_search":[seed for seed in viable if rd[str(seed)]["sol"]["status"]!="ok"],
            "repeats":r,"arms":all_stats,"effects":effects,
            "snapshot_counts":{seed:locks[seed]["fixed_count"] for seed in results},
            "by_seed_program_par2":{"groups":groups,"par2":values},"rejected":len(m["rejected"]),
            "n_queries":sum(len(x["oracle"]) for x in m["sessions"]),
            "alias_complete_pairs":int(noises.size),"alias_median_abs_ms":float(np.median(noises)*1000),
            "alias_discordant":sum(results[seed]["flips"] for seed in viable),
            "bootstrap":{"iterations":B,"seed":820202,"paired":"family; crossed with tuning seed","repeat":"averaged within program/seed before resampling"}}
    write_json(folder/"metrics.json",result)
    return result

def report_final(folder,result,manifest):
    n,f,r=result["programs"],result["families"],result["repeats"]
    seeds=result["search_seeds"]
    arm_stats=result["arms"]
    M=n*r*len(seeds)
    def fmt(value):
        return fmt_ci((value["ratio"],value["family_seed_95"]))
    wall=result["effects"]["sol-wall/rf-wall"]
    cnt=result["effects"]["sol-count/rf-count"]
    sumtext=(f"На новом закрытом тесте Sol/RF: {fmt(wall)} при одинаковом времени и {fmt(cnt)} при одинаковом числе оценок.")
    texts=[
        f"Свежий закрытый набор: {n} независимых программ, {f} семейств, {result['n_queries']} ответов за обход. Отклонено {result['rejected']} трасс. На каждый из {len(seeds)} seed настройки {r} парных повторов на программу и выбранную конфигурацию. Набор открыт после фиксации финалистов. Дубли точных векторов воспроизведены один раз на блок и привязаны ко всем соответствующим методам.",
        f"Два отдельных сравнения. «Время» включает реальный запуск Sol и общий измеренный старт, {HORIZON} с у каждого метода. «Число» ограничивает каждый метод по первым одинаковым завершённым оценкам ({', '.join(f'{s}: {v}' for s,v in result['snapshot_counts'].items())}). Каждый финалист переоценён на всех исходных валидационных программах перед открытием теста.",
        f"Интервалы учитывают целые семейства и, отдельно, парные seed настройки. Повторы сведены внутри ячеек. Из основного сравнения исключено {len(result['excluded_from_primary'])} seed без допустимого подтверждённого обратного цикла Sol. Сохранено {len(result['viable_incomplete_search'])} seed, в которых протокол обратной связи прошёл, но дальнейший поиск завершился досрочно. Дубль default: {result['alias_median_abs_ms']:.1f} мс медиана абсолютной разницы по {result['alias_complete_pairs']} решённым парам, {result['alias_discordant']} расхождений таймаута из {M}.",
        "Время измеряет использование данного CLI и решателя на данном сервере. Эта оценка включает стоимость принятия решения и задержку сервиса, а числовой срез сравнивает качество при одинаковом доступе к решателю."
    ]
    table=card_table(f"Результат на закрытом тесте · {n} программ, {len(seeds)} seed, {r} повторов, {M} ячеек на метод",
                     ["Метод и бюджет","PAR2, с","К default [95% ДИ]","Таймауты","Unknown","Ошибки","Несовпадения"],
                     [[a,f'{v["par2"]:.3f}',fmt(v["to_default"]),
                       *[v["counts"].get(k,0) for k in ("timeout","unknown","error","incorrect")]] for a,v in arm_stats.items()])
    effect=card_table("Основные парные отношения на тех же ячейках",["Сравнение","Отношение [95% ДИ, семейство и seed]","95% ДИ по семействам",
                         "Отношения по seed"],
                      [[k,fmt(v),f'{v["family_95"][0]:.3f}–{v["family_95"][1]:.3f}',
                        ", ".join(f"{s}={q:.3f}" for s,q in v["per_seed"].items())] for k,v in result["effects"].items()])
    write_json(ROOT/"evidence/06-closed-loop.json",{
        "title":"06 · Sol, RF и SMAC на новом закрытом тесте",
        "done":f"Верифицированы два сравнения бюджета на {n} новых программах и {len(seeds)} seed настройки.",
        "summary":sumtext,"paragraphs":texts,"tables":[table,effect],
        "paths":["protocols/search.json","runs/05-search/all-validation-locks.json",
                 str(manifest.relative_to(ROOT)),str((manifest.parent/"z3-audit.jsonl").relative_to(ROOT)),
                 str((manifest.parent/"release.json").relative_to(ROOT)),"runs/05-search/final/seed-<seed>/VERIFY.json",
                 "protocols/search-holdout-reserve.json",
                 "runs/05-search/final/metrics.json"],
        "recommendation":"Для выбора оптимизатора сверять оба бюджета и практическую величину выигрыша. Учитывать устойчивость по seed и различие целевых программ.",
        "next":"Проверить, сохраняются ли парные выигрыши при другом лимите времени и изменении структуры сеансов, используя отдельно зафиксированный калибровочный корпус."
    })
    build()

def validate_and_test(config):
    rd=load(BASE/"train-complete.json")["results"]
    todo={seed:shortlist(seed,SEEDS) for seed in SEEDS}
    write_json(BASE/"validation-shortlists.json",todo)
    build("Замкнутый поиск проверен. Идёт парная валидация финалистов при двух общих бюджетах.")
    with ThreadPoolExecutor(max_workers=len(todo)) as workers:
        tasks={seed:workers.submit(validation_replay,seed,core,todo[seed]) for seed,core in zip(todo,CORES)}
        locks={seed:task.result() for seed,task in tasks.items()}
    lock_file=BASE/"all-validation-locks.json"
    if not lock_file.exists():
        write_json(lock_file,{"locked":now(),"locks":locks,"protocol_sha256":digest(PLAN),
                             "train_runs_sha256":digest(BASE/"train-complete.json")})
        lock_file.chmod(0o444)
    else:
        locks={int(s):v for s,v in load(lock_file)["locks"].items()}
    target=BASE/"heldout"
    manifest=target/"manifest.json"
    priors=sorted({p.parent for p in (ROOT/"runs").glob("**/programs.json") if target not in p.parents})
    if not manifest.exists():
        try:
            manifest=materialize("search-test",lock_file,target,priors,core=CORES[0])
        except RuntimeError:
            if not manifest.exists() or not (target/"z3-audit.jsonl").exists():
                raise
    limit=load(ROOT/"protocols/search-holdout-reserve.json")["minimum_programs"]
    if load(manifest)["group_count"]<limit:
        reserve=BASE/"heldout-reserve"
        if not (reserve/"manifest.json").exists():
            manifest=materialize("search-test-reserve",lock_file,reserve,[*priors,target],core=CORES[0])
        else:
            manifest=reserve/"manifest.json"
        assert load(manifest)["group_count"]>=limit
        write_json(BASE/"admission-reserve.json",{"original_admitted":load(target/"manifest.json")["group_count"],
                   "reserve_admitted":load(manifest)["group_count"],"cause":"oracle quality before solver timing",
                   "precommit_sha256":digest(ROOT/"protocols/search-holdout-reserve.json")})
    verify_test_lock(manifest,lock_file,locks)
    final=BASE/"final"
    with ThreadPoolExecutor(max_workers=len(locks)) as workers:
        tasks=[workers.submit(test_replay,final,s,c,manifest,locks[s]) for s,c in zip(locks,CORES)]
        for t in tasks:
            t.result()
    result=tested(final,manifest,rd,locks)
    report_final(final,result,manifest)

