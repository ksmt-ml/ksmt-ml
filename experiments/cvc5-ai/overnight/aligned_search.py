"""Repeat the closed loop with identical concurrent load during the common warm start."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import sys
import time

from corpus import materialize, seal
from replay import CORES, PYTHON, ROOT, STOP, digest, load, log, now
from report import build, write_json
import search
import search_analysis as replay_final
from stability_metrics import card_table, fmt_ci

BASE=ROOT/"runs/20-aligned-search"
SCOPE=ROOT/"protocols/aligned-search.json"
SEEDS=(97301,140831)
HORIZON=1440
R=search.WARM_COUNT

def prepare():
    if SCOPE.exists():
        return load(SCOPE)
    sealed=seal("aligned-search-test",23,2,32)
    host=ROOT/"runs/13-concurrency/metrics.json"
    a=load(host)
    data={
        "locked":now(),"question":"Does the common warm-start dominance survive when warm and subsequent search are both measured with six concurrent solver workers?",
        "original_concurrency_evidence":digest(host),"actual_default_concurrency_ratio":a["effects"]["default"]["paired_ratio"],
        "continue_gate":"Only run a new live Sol comparison if the paired training retiming shows late beating warm by at least 1% in at least two original seeds.",
        "design":"Two independent search seeds; all six pinned workers concurrently measure the warm candidate set for their seed once. One preassigned stream per seed supplies exactly the same warm observations to all arms. Other warm streams measure calibration variability. All arms charged the full common wall elapsed. Sol/RF/native SMAC then each use one pinned worker, same common start and pool protocol.",
        "target_budget_s":HORIZON,"search_seeds":SEEDS,"evaluation_count_cutoff":search.MATCHED_COUNT,
        "core_assignments":list(CORES),"warm_feeding_streams":{"97301":0,"140831":1},
        "arm_order":["sol","rf","smac"],"warm_measurements_per_stream":R,
        "source_closed_loop_protocol_sha256":digest(search.PLAN),
        "guard":{"minimum_time_remaining_s":3300,"max_measurement_stop":"2026-11-22T10:48:00+03:00"},
        "dataset_seed_commitment":sealed["seed_commitment_sha256"],
        "scope_limits":"Different seeds and new cohort; compare methods within this protocol. Change of warm load is a controlled design improvement; cross-experiment comparisons also involve new seeds, and no new model settings."
    }
    write_json(SCOPE,data)
    SCOPE.chmod(0o444)
    return load(SCOPE)

def cmd(path,*args):
    return [PYTHON,"-B",__file__,str(path),*map(str,args)]

def warm(s):
    expected=load(search.SMOKE/"VERIFY.json")
    assert expected["ok"]
    BASE.mkdir(exist_ok=True)
    flag=BASE/"warm-lock.json"
    if flag.exists():
        return load(flag)
    commands=[]
    for p,seed in enumerate(SEEDS):
        folder=BASE/f"seed-{seed}"
        folder.mkdir(exist_ok=True)
        for j in range(3):
            path=folder/f"warm-stream-{j}"
            path.mkdir(exist_ok=True)
            commands.append((cmd(path,"warm",seed,CORES[p*3+j]),path/"warm-wrapper.out"))
    start=time.monotonic()
    results=search.execute_workers(commands,440)
    assert not any(results), results
    end=time.monotonic()
    time_cost=end-start
    record={}
    for p,seed in enumerate(SEEDS):
        base=BASE/f"seed-{seed}"
        expected_plan=None
        streams=[]
        for j in range(3):
            path=base/f"warm-stream-{j}"
            original=load(path/"warm/locked.json")
            verified=search.verify_training(path,"warm")
            ph=digest(path/"proposals.json")
            if expected_plan is not None:
                assert ph==expected_plan
            expected_plan=ph
            streams.append({"j":j,"source_wall_s":original["wall_s"],"core":original["core"],"train":verified})
        idx=s["warm_feeding_streams"][str(seed)]
        obs=base/f"warm-stream-{idx}"
        shutil.copytree(obs/"warm",base/"warm")
        shutil.copy2(obs/"proposals.json",base/"proposals.json")
        before=load(base/"warm/locked.json")
        assert before["candidates_sha256"]==digest(base/"warm/candidates.jsonl")
        write_json(base/"warm/locked.json",{**before,"wall_s":time_cost,
                 "physical_source_wall_s":before["wall_s"],"baseline_stream":idx})
        record[str(seed)]={"streams":streams,"selected":idx,"warm_history_hash":before["candidates_sha256"]}
    doc={"locked":now(),"charged_warm_wall_s":time_cost,"seed_streams":record,"scope_sha256":digest(SCOPE)}
    write_json(flag,doc)
    return doc

def arms(s):
    out=BASE/"train-complete.json"
    if out.exists():
        return load(out)["results"]
    commands=[]
    for p,seed in enumerate(SEEDS):
        f=BASE/f"seed-{seed}"
        for j in range(3):
            arm=search.ARMS[(j+p)%3]
            commands.append((cmd(f,"arm",seed,arm,CORES[3*p+j]),f/f"{arm}.wrapper.out"))
    log(f"START aligned closed loop, {SEEDS}, {HORIZON} seconds each")
    codes=search.execute_workers(commands,HORIZON+30)
    assert not any(codes),codes
    results={}
    for seed in SEEDS:
        folder=BASE/f"seed-{seed}"
        results[str(seed)]={}
        for role in search.ARMS:
            verified=search.verify_training(folder,role)
            entry=load(folder/role/"status.json")
            if role in ("sol","rf"):
                cert=replay_final.audit_decisions(seed,role)
            else:
                cert={}
            results[str(seed)][role]={**entry,"verified":verified,"feedback":cert}
    write_json(out,{"complete":now(),"results":results})
    return results

def holdout(updated):
    slate={seed:replay_final.shortlist(seed,SEEDS) for seed in SEEDS}
    write_json(BASE/"validation-shortlists.json",slate)
    gate=BASE/"all-validation-locks.json"
    if not gate.exists():
        with ThreadPoolExecutor(max_workers=2) as workers:
            tasks={seed:workers.submit(replay_final.validation_replay,seed,core,slate[seed])
                   for seed,core in zip(SEEDS,CORES)}
            locked={seed:task.result() for seed,task in tasks.items()}
        write_json(gate,{"locked":now(),"locks":locked,"scope_sha256":digest(SCOPE)})
        gate.chmod(0o444)
    else:
        locked={int(s):k for s,k in load(gate)["locks"].items()}
    target=BASE/"heldout"
    manifest=target/"manifest.json"
    if not manifest.exists():
        exclusions=sorted({p.parent for p in (ROOT/"runs").glob("**/programs.json") if target not in p.parents})
        manifest=materialize("aligned-search-test",gate,target,exclusions,core=CORES[0])
    replay_final.verify_test_lock(manifest,gate,locked)
    directory=BASE/"final"
    with ThreadPoolExecutor(max_workers=2) as workers:
        ts=[workers.submit(replay_final.test_replay,directory,seed,core,manifest,locked[seed])
            for seed,core in zip(SEEDS,CORES)]
        for t in ts:
            t.result()
    tested=replay_final.tested(directory,manifest,updated,locked)
    return tested,locked

def report(measured,locks,after,warm_data):
    r=measured
    n=r["programs"]
    W=r["effects"]["sol-wall/rf-wall"]
    C=r["effects"]["sol-count/rf-count"]
    same=sum(len({search.canonical(row["config"]) for row in m["selected"].values()})==1 for m in locks.values())
    headline=f"С общей шестиядерной разминкой Sol/RF дал {fmt_ci((W['ratio'],W['family_seed_95']))} при равном времени и {fmt_ci((C['ratio'],C['family_seed_95']))} при равном числе оценок."
    all_rows=[]
    for seed,arms in after.items():
        for a,sta in arms.items():
            all_rows.append([seed,a,sta["completed"],f'{sta["logical_wall_s"]:.1f}',f'{sta["best_train"]:.3f}',
                             sta["feedback"].get("valid_model_calls","—")])
    write_json(ROOT/"evidence/21-aligned-search.json",{
        "title":"21 · Замкнутый поиск с согласованным фоном разминки",
        "done":"Повторный замкнутый поиск с реальными моделями проверен на отдельном закрытом наборе.",
        "summary":headline,
        "paragraphs":[
            f"Заново сравнили две независимые серии seed. Все шесть закреплённых воркеров работали и во время общей разминки. Каждая настройка измерялась на исходных 15 программах по одному разу в каждом из трёх потоков на seed. По заранее назначенному потоку сформирована общая история из {R} вариантов. Полная разминка {warm_data['charged_warm_wall_s']:.1f} с входит в бюджет {HORIZON} с каждого метода.",
            f"Финальный независимый тест: {n} новых программ, {r['families']} семейств, {r['repeats']} повторов. Отдельно срез равного времени и {search.MATCHED_COUNT} реальных оценок. Среди двух seed совпадение всех выбранных векторов в {same}. В основных отношениях использованы допустимые seed с реальной обратной связью ({len(r['search_seeds'])}).",
            "Внутри этого опыта сравниваются одинаковые правила, общий измеренный старт и сопоставимый фон начала поиска. Совпадение или изменение относительно прежнего опыта также зависит от новых seed и другого закрытого набора."
        ],
        "tables":[card_table("Завершённый поиск · 15 программ на каждого кандидата",
                           ["Seed","Метод","Оценок","Время, с","Лучший train PAR2, с","Валидные ответы Sol"],all_rows),
                  card_table(f"Закрытая проверка · {n} программ × {r['repeats']} повторов × {len(r['search_seeds'])} допустимых seed",
                           ["Режим","PAR2, с","К default [95% ДИ]","Таймауты","Ошибки + unknown + несовпадения"],
                           [[k,f"{v['par2']:.3f}",fmt_ci((v["to_default"]["ratio"],v["to_default"]["family_seed_95"])),
                             v["counts"].get("timeout",0),sum(v["counts"].get(x,0) for x in ("error","unknown","incorrect"))]
                            for k,v in r["arms"].items()])],
        "paths":["protocols/aligned-search.json","runs/20-aligned-search/warm-lock.json",
                 "runs/20-aligned-search/train-complete.json","runs/20-aligned-search/all-validation-locks.json",
                 "runs/20-aligned-search/heldout/manifest.json","runs/20-aligned-search/final/metrics.json"],
        "recommendation":"В следующих сравнительных поисках фиксировать нагрузку общей разминки по тому же расписанию, что и основной цикл.",
        "next":"Увеличить независимый размер сравнения, если величина выигрыша практична на целевых клиентах."
    })
    build()

def main():
    scope=prepare()
    assert STOP-time.time()>=3300
    replay_final.BASE=BASE
    replay_final.HORIZON=HORIZON
    replay_final.SEEDS=SEEDS
    init=warm(scope)
    measured=arms(scope)
    data,locks=holdout(measured)
    report(data,locks,measured,init)

if __name__=="__main__":
    if len(sys.argv)>1:
        base=Path(sys.argv[1])
        task=sys.argv[2]
        seed=int(sys.argv[3])
        if task=="warm":
            search.train_warm(base,seed,int(sys.argv[4]))
        else:
            search.run_arm(base,seed,sys.argv[4],int(sys.argv[5]),horizon=HORIZON)
    else:
        main()
