"""Select and replay an executable two-attempt schedule with a true total cap."""
from collections import Counter
from pathlib import Path

import numpy as np

from corpus import materialize, seal
from portfolio import BASE as PORT, MENU, LOCK, compatible, frac, group_draws
from replay import CORES, ROOT, digest, launch, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE=ROOT/"runs/09-restart"
SCOPE=ROOT/"protocols/restart.json"
REPEATS=8
TOTAL=1.50
FIRST=(.20,.33,.50,.70,.90)
OVERHEAD=.005

def freeze():
    commit=seal("restart-test",24,2,34)
    if SCOPE.exists():
        return load(SCOPE)
    menu=load(MENU)
    selector=load(LOCK)
    _,groups,arms,score,status,wall=replay_cube(PORT/"dev-replay")
    m=load(PORT/"dev-corpus/manifest.json")
    fam_by={r["group"]:r["family"] for r in m["sessions"]}
    fam=np.array([fam_by[g] for g in groups])
    names=selector["eligible"]
    C=[arms.index(a) for a in names]
    policies=[]
    estimates=[]
    # Rotate the second observation by half of the complete repeat count. Each
    # prediction pays the first slice when it times out, irrespective of the
    # outcome of the full baseline replay.
    rotation=wall.shape[2]//2
    for a in C:
        for b in C:
            if a==b:
                continue
            for cap in FIRST:
                wa,sa=wall[:,a,:],status[:,a,:]
                wb=np.roll(wall[:,b,:],rotation,axis=1)
                sb=np.roll(status[:,b,:],rotation,axis=1)
                is_good=(sa=="solved")&(wa<=cap)
                allow=(sa=="solved")|(sa=="timeout")
                out=np.where(is_good,wa,
                     np.where(allow&(sb=="solved")&(wb+cap+OVERHEAD<=TOTAL),wb+cap+OVERHEAD,2*TOTAL))
                estimates.append(out.mean(axis=1))
                policies.append({"first":arms[a],"second":arms[b],"first_s":cap})
    predictions=np.stack(estimates,axis=1)
    best=int(np.argmin(predictions.mean(axis=0)))
    baseline=score[:,arms.index(menu["global_arm"]),:].mean(axis=1)
    cv=np.zeros(len(groups))
    folds={}
    for family in sorted(set(fam)):
        mask=fam==family
        i=int(np.argmin(predictions[~mask].mean(axis=0)))
        cv[mask]=predictions[mask,i]
        folds[str(family)]=policies[i]
    winner=policies[best]
    global_arm=menu["global_arm"]
    solo=min(names,key=lambda a:(score[:,arms.index(a),:].mean(),a))
    record={
        "locked":now(),"hypothesis":"A short first attempt followed by a distinct restart may recover cross-repeat oracle headroom under a true 1.50 s total cap.",
        "precommit_sha256":commit["seed_commitment_sha256"],"menu_sha256":digest(MENU),"dev_sha256":digest(PORT/"dev-replay/metrics.json"),
        "procedure":"Use only portfolio-dev completed matrix. Approximate the second attempt by disjoint measured repeat, charge the first slice and 5 ms overhead. Freeze best schedule before any new trace.",
        "first_s_grid":FIRST,"total_s":TOTAL,"development_policies":policies,
        "development_par2":[float(x) for x in predictions.mean(axis=0)],"development_cv_folds":folds,
        "development_cv_par2":float(cv.mean()),"development_global_par2":float(baseline.mean()),
        "development_frozen_par2":float(predictions[:,best].mean()),
        "primary_schedule":winner,"config_vectors":{k:compatible(v) for k,v in menu["arms"].items()},
        "global":global_arm,"best_single_development":solo,"repeats":REPEATS,
        "evaluation":"fresh real cvc5 attempts. At most one restart after timeout. Errors and wrong answers disqualify. Time for both attempts inside the same wall clock cap."
    }
    write_json(SCOPE,record)
    SCOPE.chmod(0o444)
    return record

def publish(directory,manifest,lock):
    v=load(directory/"VERIFY.json")
    _,groups,arms,score,status,wall=replay_cube(directory)
    m=load(manifest)
    f={r["group"]:r["family"] for r in m["sessions"]}
    families=[f[g] for g in groups]
    mat=group_draws(families,np.random.default_rng(34218))
    n,a,r=score.shape
    cell={k:score[:,i].mean(axis=1) for i,k in enumerate(arms)}
    outcomes={k:{"par2":float(y.mean()),"to_global":frac(y,cell["global"],mat),"to_default":frac(y,cell["default"],mat),
                "counts":v["counts"][k]} for k,y in cell.items()}
    from replay import lines
    actual=[r for p in sorted(directory.glob("worker-*.jsonl")) for r in lines(p) if r["arm"]=="restart"]
    attempts=Counter()
    for rec in actual:
        attempts["second_attempts"]+=len(rec["attempts"])==2
        attempts["second_solved"]+=len(rec["attempts"])==2 and rec["status"]=="solved"
        attempts["first_solved"]+=len(rec["attempts"])==1 and rec["status"]=="solved"
        attempts["wrong_intermediate_answers"]+=sum(a["wrong_answers"] for a in rec["attempts"])
    result={"programs":n,"families":len(set(families)),"repeats":r,"total_rows":v["rows"],
            "results":outcomes,"attempts":attempts,"scope_sha256":digest(SCOPE),
            "rejected":len(m["rejected"]),"program_par2":{g:{k:float(y[i]) for k,y in cell.items()} for i,g in enumerate(groups)}}
    write_json(directory/"metrics.json",result)
    sched=lock["primary_schedule"]
    summary=(f"Реальный перезапуск при общем бюджете 1,50 с дал отношение "
             f"{fmt_ci(outcomes['restart']['to_default'])} к default на {n} новых программах.")
    write_json(ROOT/"evidence/10-restart.json",{
        "title":"10 · Исполнимый портфель с общим лимитом",
        "done":f"Измерен реальный перезапуск cvc5 на {n} новых независимых Java-программах.",
        "summary":summary,
        "paragraphs":[
            f"Порядок выбран только на разработке. Сначала {sched['first']} получает {sched['first_s']:.2f} с, затем при таймауте {sched['second']} получает реальный остаток от 1,50 с. Повтор начинает поток заново. Задержка запуска обоих процессов входит в PAR2. Ошибочный либо некорректный ответ не даёт права на перезапуск.",
            f"На разработке приближение CV по семействам оценило PAR2 {lock['development_cv_par2']:.3f} с против {lock['development_global_par2']:.3f} с у глобального воспроизведения. Модель CV предварительно оплачивала первый срез, повторы были взяты из разных половин. Закрытый тест измеряет фактические попытки.",
            f"Новые {n} программ, {len(set(families))} семейств, {r} повторов на ячейку. {attempts['second_attempts']} повторных попыток, {attempts['second_solved']} завершились в остаток времени. Промежуточных неправильных ответов {attempts['wrong_intermediate_answers']}. Интервалы парно пересэмплируют семейства.",
            "Режим исполним для записанного полного потока. Живой клиент USVM потребовал бы журнал операций, восстановление контекста и отдельную проверку задержки."
        ],
        "tables":[card_table(f"Реальный портфель · {n} программ, по {n*r} запусков каждого режима",
                   ["Режим","PAR2, с","К default [95% ДИ]","К глобальному [95% ДИ]",
                    "Таймауты","Ошибки + unknown + несовпадения"],
                   [[k,f'{s["par2"]:.3f}',fmt_ci(s["to_default"]),fmt_ci(s["to_global"]),s["counts"].get("timeout",0),
                     sum(s["counts"].get(z,0) for z in ("error","incorrect","unknown"))] for k,s in outcomes.items()])],
        "paths":["protocols/restart.json","runs/09-restart/corpus/manifest.json","runs/09-restart/replay/plan.json",
                 "runs/09-restart/replay/worker-{0..5}.jsonl","runs/09-restart/replay/VERIFY.json","runs/09-restart/replay/metrics.json"],
        "recommendation":"Сравнить полезность перезапуска с его стоимостью при рабочем таймауте. Учитывать возможность воспроизвести лог состояния.",
        "next":"Проверить перенос наиболее содержательного контраста на другой набор библиотек или на заранее заданный более трудный профиль программ."
    })
    build()

def main():
    lock=freeze()
    target=BASE/"corpus"
    manifest=target/"manifest.json"
    if not manifest.exists():
        priors=[PORT/"dev-corpus",PORT/"test-corpus",ROOT/"runs/05-search/heldout",
             ROOT/"runs/06-cap-calibration/corpus",ROOT/"runs/07-solver-seeds/corpus",ROOT/"runs/08-prefix/corpus"]
        manifest=materialize("restart-test",SCOPE,target,priors,core=CORES[0])
    vals=lock["config_vectors"]
    policy=lock["primary_schedule"]
    settings={"default":{},"default_alias":{},"global":vals[lock["global"]],
              "development_best":vals[lock["best_single_development"]],
              "restart":vals[policy["first"]]}
    directory=BASE/"replay"
    if not (directory/"VERIFY.json").exists():
        launch(directory,manifest,settings,REPEATS,227321,1800,
               fallback_by_arm={"restart":{"first_s":policy["first_s"],"second":vals[policy["second"]]}})
    publish(directory,manifest,lock)
if __name__=="__main__":
    from recovery import restore_measurement_start
    restore_measurement_start()
    main()
