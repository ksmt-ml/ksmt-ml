"""Check whether charging partial progress changes the config shortlist under PAR2."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from corpus import materialize, seal
from portfolio import frac, group_draws
from replay import CORES, DATA, ROOT, digest, launch, lines, load, now
from report import build, write_json
from search import ARMS, BASE as SEARCH, HORIZON, SEEDS, canonical
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE=ROOT/"runs/19-progress-objective"
SCOPE=ROOT/"protocols/progress-objective.json"
MARGIN=0.75
VAL_REPEATS=4
TEST_REPEATS=6

def available(s):
    base=SEARCH/f"seed-{s}"
    observations=defaultdict(list)
    param={}
    for method in ("warm",*ARMS):
        scope=base/method
        candidates={r["candidate_id"]:r for r in lines(scope/"candidates.jsonl")
                    if r["complete"] and r["eligible"] and r["finish_s"]<=HORIZON}
        by=defaultdict(list)
        for obs in lines(scope/"sessions.jsonl"):
            if obs["candidate_id"] in candidates:
                by[obs["candidate_id"]].append(obs)
        for n,cand in candidates.items():
            key=canonical(cand["config"])
            rows=by[n]
            assert len(rows)==15
            mean=float(np.mean([r["par2"] for r in rows]))
            assert abs(mean-cand["par2"])<1e-12
            useful=float(np.mean([min(1,r["correct_answers"]/r["queries"]) if r["status"]=="timeout" else 0 for r in rows]))
            observations[key].append((mean,mean-MARGIN*useful,useful))
            param[key]=cand["config"]
    scoring={k:{"params":param[k],"par2":float(np.mean(z,axis=0)[0]),
                "progress":float(np.mean(z,axis=0)[1]),
                "prefix_fraction":float(np.mean(z,axis=0)[2]),"measurements":len(z)} for k,z in observations.items()}
    idx={"default":{}}
    reverse={canonical({}):"default"}
    group={}
    for score in ("par2","progress"):
        best=sorted(scoring,key=lambda k:(scoring[k][score],k))[:3]
        keys=[]
        for k in best:
            if k not in reverse:
                label=f"Q{len(idx):02d}"
                reverse[k]=label
                idx[label]=scoring[k]["params"]
            keys.append(reverse[k])
        group[score]=list(dict.fromkeys(["default",*keys]))
    return {"configs":idx,"choices":group,"candidate_pool":len(scoring),
            "scored_options":{reverse[k]:scoring[k] for k in reverse}}

def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    original=load(SEARCH/"all-validation-locks.json")["locks"]
    assert all(len({canonical(entry["config"]) for entry in d["selected"].values()})==1 for d in original.values())
    sealed=seal("progress-objective-test",22,2,30)
    menus={s:available(s) for s in SEEDS}
    data={
        "locked":now(),"question":"Were useful later candidates excluded because the full-session PAR2 objective ignored correct partial answers before timeout?",
        "method":"Post-hoc use of completed, real training observations only. Same per-seed pools with duplicate vectors averaged before scoring. Ordinary PAR2 versus PAR2 minus 0.75s times exact fraction of expected answers reached in timeout sessions. Solver correctness and reported test PAR2 retain original contract.",
        "selection":"Top three and default for each objective, same interleaved validation on original 11 programs. Pick lowest validation PAR2. Open new family-disjoint cohort only if actual validated vectors differ for at least one seed.",
        "sampling":"Paired runs use original validation and independent fresh test. Four seed cohorts share same new test set.",
        "seed_commitment_sha256":sealed["seed_commitment_sha256"],"train_snapshot_sha256":digest(SEARCH/"train-complete.json"),
        "new_candidate_generation":False,"selected_seeds":list(SEEDS),"choices":menus,
    }
    write_json(SCOPE,data)
    SCOPE.chmod(0o444)
    return load(SCOPE)

def val(s,core,spec):
    root=BASE/f"validation-{s}"
    if not (root/"VERIFY.json").exists():
        launch(root,DATA/"validation/manifest.json",spec["configs"],VAL_REPEATS,s^661033,1000,cores=(core,))
    verified=load(root/"VERIFY.json")
    result={}
    for score,slate in spec["choices"].items():
        eligible=[x for x in slate if not any(verified["counts"][x].get(k,0) for k in ("error","incorrect","unknown"))]
        best=min(eligible,key=lambda x:(verified["par2"][x],x))
        result[score]={"arm":best,"params":spec["configs"][best],"validation":verified["par2"][best]}
    return result

def maybe_test(lock):
    artifact=BASE/"selections.json"
    if not any(d["progress"]["params"]!=d["par2"]["params"] for d in lock["results"].values()):
        return None,None
    corpus=BASE/"heldout"
    m=corpus/"manifest.json"
    if not m.exists():
        priors=sorted({p.parent for p in (ROOT/"runs").glob("**/programs.json") if corpus not in p.parents})
        m=materialize("progress-objective-test",artifact,corpus,priors,core=CORES[0])
    collected={}
    with ThreadPoolExecutor(max_workers=len(SEEDS)) as worker:
        tasks=[]
        for s,core in zip(SEEDS,CORES):
            spec=lock["results"][str(s)]
            params={"default":{}}
            lookup={canonical({}):"default"}
            collected[s]={}
            for a in ("par2","progress"):
                p=spec[a]["params"]
                k=canonical(p)
                if k not in lookup:
                    lookup[k]=a
                    params[a]=p
                collected[s][a]=lookup[k]
            path=BASE/f"test-{s}"
            if not (path/"VERIFY.json").exists():
                tasks.append(worker.submit(launch,path,m,params,TEST_REPEATS,s^488913,980,cores=(core,)))
            write_json(path/"mapping.json",collected[s]) if (path/"VERIFY.json").exists() else None
        for future in tasks:
            future.result()
    arrays=[]
    queries={}
    labels=[]
    for s in SEEDS:
        path=BASE/f"test-{s}"
        write_json(path/"mapping.json",collected[s])
        _,groups,arms,cube,st,wall=replay_cube(path)
        series=[]
        for a in ("par2","progress"):
            ix=arms.index(collected[s][a])
            series.append(cube[:,ix,:].mean(axis=1))
            q=Counter(st[:,ix,:].ravel())
            labels.append([s,a,f"{series[-1].mean():.3f}",q["timeout"],q["error"],q["unknown"],q["incorrect"]])
        arrays.append(series)
    V=np.asarray(arrays)
    f={r["group"]:r["family"] for r in load(m)["sessions"]}
    rng=np.random.default_rng(72531)
    draws=group_draws([f[g] for g in groups],rng)
    crossed=rng.multinomial(len(SEEDS),np.full(len(SEEDS),1/len(SEEDS)),size=B)
    dist=np.sum((draws@V[:,1,:].T)*crossed,axis=1)/np.sum((draws@V[:,0,:].T)*crossed,axis=1)
    out={"programs":len(groups),"families":len(set(f.values())),"repeats":TEST_REPEATS,
         "paired_seed_count":len(SEEDS),
         "progress_to_standard":[float(V[:,1].mean()/V[:,0].mean()),np.percentile(dist,[2.5,97.5]).tolist()],
         "raw_validation_locks_sha256":digest(artifact)}
    write_json(BASE/"metrics.json",out)
    return out,labels

def main():
    s=freeze()
    BASE.mkdir(exist_ok=True)
    lock=BASE/"selections.json"
    if not lock.exists():
        with ThreadPoolExecutor(max_workers=len(SEEDS)) as w:
            tasks={seed:w.submit(val,seed,cpu,s["choices"][str(seed)]) for seed,cpu in zip(SEEDS,CORES)}
            r={seed:p.result() for seed,p in tasks.items()}
        write_json(lock,{"locked":now(),"results":r,"scope_sha256":digest(SCOPE)})
        lock.chmod(0o444)
    q=load(lock)
    tested,data=maybe_test(q)
    n=sum(d["progress"]["params"]!=d["par2"]["params"] for d in q["results"].values())
    title=f"Учёт частичных ответов оставил другой финал на {n} из четырёх seed."
    if tested:
        title+=f" Закрытое отношение к обычному отбору {fmt_ci(tested['progress_to_standard'])}."
    rows=[[k,s["choices"][k]["candidate_pool"],v["par2"]["arm"],v["progress"]["arm"],
        f'{v["par2"]["validation"]:.3f}',f'{v["progress"]["validation"]:.3f}'] for k,v in q["results"].items()]
    tables=[card_table(f"Исходная валидация · 11 программ, по {VAL_REPEATS} повторов",
                       ["Seed","Кандидатов в архиве","Финал обычного PAR2","Финал с учётом префикса","Обычный PAR2, с","С префиксом, с"],rows)]
    if tested:
        tables.append(card_table(f"Свежий закрытый тест · {tested['programs']} программ, {TEST_REPEATS} повторов и 4 seed",
                                 ["Seed","Правило шорт-листа","PAR2, с","Таймауты","Ошибки","Unknown","Несовпадения"],data))
    write_json(ROOT/"evidence/20-progress-objective.json",{
        "title":"20 · Учёт решённых проверок в прерванном сеансе",
        "done":"Проверено, меняет ли промежуточный прогресс выбор финалиста при том же архиве обучения.",
        "summary":title,
        "paragraphs":[
            "Сравниваются правила формирования списка финалистов на одном и том же архиве уже измеренного обучения. У таймаута второй критерий вычитает 0,75 с, умноженные на долю ответов, успевших совпасть с оракулом. Дубли векторов усреднены. Тестовый PAR2 сохраняет исходный штраф.",
            f"На входе четырёх seed ровно те же 15 программ. На уровне выбора обоих критериев один и тот же парный повтор на 11 исходных программах, {VAL_REPEATS} повторов на ячейку, до фиксации всех настроек.",
            (f"Проверка на {tested['programs']} новых программах и {tested['families']} семействах независима от подбора. ДИ пересэмплирует семейства и seed." if tested else
             "Валидационные финалисты совпали. Закрытый резерв по заранее заданному правилу не открывался.")
        ],
        "tables":tables,
        "paths":["protocols/progress-objective.json","runs/19-progress-objective/selections.json",
                 "runs/19-progress-objective/<срез>/VERIFY.json",
                 *(["runs/19-progress-objective/metrics.json","runs/19-progress-objective/heldout/manifest.json"] if tested else [])],
        "recommendation":"Сохранять полный PAR2 как метрику инструкции клиента. Отдельно использовать прогресс только для формирования кандидатов, если независимый выбор улучшает полные тайминги.",
        "next":"Проверить пригодность этого критерия на реальном клиенте с продуктовым лимитом, если срок позволяет."
    })
    build()
if __name__=="__main__":
    main()
