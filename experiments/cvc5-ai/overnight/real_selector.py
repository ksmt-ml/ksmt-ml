"""Fit a strictly pre-check routing rule on released library cohorts for new projects."""
from collections import Counter
import re
import time

import numpy as np

from portfolio import MENU, fit, frac, group_draws
from prefix_probe import OPERATORS, prefix
from replay import ROOT, digest, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

LOCK=ROOT/"protocols/library-trained-selector.json"
DEDUP=ROOT/"protocols/library-selector-cross-source-dedup.json"
REAL=ROOT/"runs/12-new-libraries"
BRIDGE=ROOT/"runs/11b-array-bridge"

def descriptor(tag):
    value=tag.split("(",1)[1].split(")",1)
    params,out=value
    types=re.findall(r"\[*L[^;]+;|\[*[ZBCSIJFD]",params)
    return [len(types),sum(t.startswith("[") for t in types),
            sum(t.lstrip("[").startswith("L") for t in types),
            sum(t.lstrip("[") in ("F","D") for t in types),
            len(params),int(out.startswith("[")),int(out.startswith("L"))]

def designs(manifest,groups,trace_manifest=None):
    seen={row["group"]:row for row in load(manifest)["sessions"]}
    trace_manifest=trace_manifest or manifest
    trace_rows={row["group"]:row for row in load(trace_manifest)["sessions"]}
    matrix=[]
    fs=[]
    hs={}
    tick=time.perf_counter_ns()
    for g in groups:
        row=seen[g]
        actual=trace_rows[g]
        path=trace_manifest.parent/actual["trace"]
        if trace_manifest!=manifest:
            assert actual["sha256"]==row["original_sha256"]
        vector,short_hash=prefix(path)
        features=descriptor(row["tag"])
        matrix.append([*features,*vector])
        fs.append(row["family"])
        hs[g]=short_hash
    return np.asarray(matrix,dtype=float),np.array(fs),hs,(time.perf_counter_ns()-tick)/1000/len(groups)

def predict_delta(model,x):
    return (((x-model["mean"])/model["sd"])@model["coef"]+model["bias"]).reshape(-1)

def prepare():
    if LOCK.exists():
        return load(LOCK)
    menu=load(MENU)
    global_arm=menu["global_arm"]
    branch=load(BRIDGE/"replay/metrics.json")["next_encoding"]
    assert branch in ("tuple","nested")
    archived=ROOT/"runs/04-transfer/corpus-recovered/manifest.json"
    sources=[(BRIDGE/"replay",BRIDGE/branch/"paired-manifest.json",branch,branch+"_global",archived),
             (REAL/"replay",REAL/"portable-corpus/manifest.json","default",global_arm,None)]
    all_x=[]
    all_f=[]
    all_y=[]
    cohorts=[]
    fingerprints={}
    for folder,m,base,arm,trace_origin in sources:
        _,groups,after,score,st,_=replay_cube(folder)
        matrix,families,cat,us=designs(m,groups,trace_origin)
        all_x.append(matrix)
        all_f.extend(families.tolist())
        all_y.append(np.stack([score[:,after.index(n),:].mean(axis=1) for n in (base,arm)],axis=1))
        cohorts.append({"programs":len(groups),"projects":len(set(families)),"repeats":load(folder/"VERIFY.json")["repeats"],
                        "measured_manifest":str(m.relative_to(ROOT)),"source_hash":digest(m),
                        "raw_trace_manifest_sha256":digest(trace_origin or m),
                        "verify_hash":digest(folder/"VERIFY.json"),"features_us":us})
        fingerprints.update(cat)
    x=np.vstack(all_x)
    fam=np.array(all_f)
    y=np.vstack(all_y)
    delta=(y[:,1]-y[:,0]).reshape(-1,1)
    cv=[]
    for lam in (8.,32.,128.):
        p=np.zeros(len(fam))
        for g in set(fam):
            other=fam!=g
            p[~other]=predict_delta(fit(x[other],delta[other],lam),x[~other])
        for threshold in (0.,.01,.05,.15):
            select=(p < -threshold).astype(int)
            val=float(y[np.arange(len(y)),select].mean())
            cv.append({"l2":lam,"threshold_ms":threshold*1000,"value":val,"global_picks":int(np.sum(select==1))})
    ranked=min(cv,key=lambda r:(r["value"],-r["l2"],-r["threshold_ms"]))
    glob=float(y[:,1].mean())
    data={"locked":now(),"question":"Does training a pre-check binary selector on library sessions generalize better than always using the same frozen configuration?",
          "arrays_mode":branch,"global":global_arm,"model":fit(x,delta,ranked["l2"]),
          "threshold_s":ranked["threshold_ms"]/1000,"fallback_global":ranked["value"]>=glob-.010,
          "cv_global_par2":glob,"cv_default_par2":float(y[:,0].mean()),"cv_rule_par2":ranked["value"],"cv":cv,
          "training_projects":sorted(set(fam)),"training_programs":len(fam),"cohorts":cohorts,
          "prefix_sha256_by_program":fingerprints,
          "features":"Java JVM method descriptor plus the frozen first-check event prefix before the first solver check; no timeout, library ID, dynamic answers, or full-trace counts",
          "protocol":"Existing published library experiments are development only for this new question. Hold out all five entirely new projects in arrays-options. Global menu, fitted weights and thresholds are locked before those traces are created."}
    write_json(LOCK,data)
    LOCK.chmod(0o444)
    return data

def freeze_evaluation_identity(manifest):
    lock=load(LOCK)
    source=load(manifest)
    if DEDUP.exists():
        chosen=load(DEDUP)
        assert chosen["test_manifest_sha256"]==digest(manifest)
        assert chosen["training_lock_sha256"]==digest(LOCK)
        return chosen
    seen={}
    for cohort in lock["cohorts"]:
        path=ROOT/cohort["measured_manifest"]
        assert digest(path)==cohort["source_hash"]
        for item in load(path)["sessions"]:
            seen[item["sha256"]]=item["group"]
    repeated=[{"test":item["group"],"train":seen[item["sha256"]],"sha256":item["sha256"]}
              for item in source["sessions"] if item["sha256"] in seen]
    chosen={"locked":now(),"test_manifest_sha256":digest(manifest),
            "training_lock_sha256":digest(LOCK),"excluded_exact_matches":repeated}
    write_json(DEDUP,chosen)
    DEDUP.chmod(0o444)
    return chosen

def evaluate(folder,manifest):
    lock=load(LOCK)
    dedup=freeze_evaluation_identity(manifest)
    excluded={item["test"] for item in dedup["excluded_exact_matches"]}
    _,all_groups,arms,score,status,_=replay_cube(folder)
    take=[i for i,g in enumerate(all_groups) if g not in excluded]
    groups=[all_groups[i] for i in take]
    score=score[take]
    status=status[take]
    x,fam,hashes,micros=designs(manifest,groups)
    assert not set(fam)&set(lock["training_projects"])
    labels=("default","global")
    ref=arms.index(labels[0])
    alt=arms.index(labels[1])
    preds=predict_delta(lock["model"],x)
    choice=np.where(preds < -lock["threshold_s"],alt,ref)
    deploy=np.full(len(groups),alt) if lock["fallback_global"] else choice
    count=len(groups)
    r=score.shape[2]
    d=score[:,[ref,alt],:].mean(axis=2)
    c=score.mean(axis=2)
    idx=np.arange(count)
    f=score[:,[ref,alt],:r//2].mean(axis=2)
    b=score[:,[ref,alt],r//2:].mean(axis=2)
    oracle=(f[idx,np.argmin(b,axis=1)]+b[idx,np.argmin(f,axis=1)])/2
    variants={"default":d[:,0],"fixed_global":d[:,1],"learned":c[idx,choice],
              "locked_policy":c[idx,deploy],"cross_repeat_oracle":oracle}
    draws=group_draws(fam,np.random.default_rng(61249))
    stats={}
    for label,vec in variants.items():
        q={"par2":float(vec.mean()),"to_global":frac(vec,d[:,1],draws),
           "to_default":frac(vec,d[:,0],draws)}
        if label=="learned":
            q["timeouts"]=int(np.sum(status[idx,choice,:]=="timeout"))
        elif label=="locked_policy":
            q["timeouts"]=int(np.sum(status[idx,deploy,:]=="timeout"))
        elif label in ("default","fixed_global"):
            q["timeouts"]=int(np.sum(status[:,ref if label=="default" else alt,:]=="timeout"))
        stats[label]=q
    out={"programs":count,"families":len(set(fam)),"repeats":r,"statistics":stats,"features_us":micros,
         "global_programs_raw_rule":int(np.sum(choice==alt)),"global_programs_policy":int(np.sum(deploy==alt)),
         "excluded_exact_training_streams":dedup["excluded_exact_matches"],
         "prefix_sha256_by_program":hashes,"evaluation_projects":sorted(set(fam)),"lock_sha256":digest(LOCK),
         "per_program":{g:{"case":"global" if d==alt else "default","predicted_delta_s":float(preds[i])}
                        for i,(g,d) in enumerate(zip(groups,deploy))}}
    write_json(folder/"real-selector-metrics.json",out)
    text=f"На {count} новых библиотечных методах правило, обученное на прежних библиотеках, дало {fmt_ci(stats['learned']['to_global'])} к постоянному C05."
    write_json(ROOT/"evidence/18b-library-selector.json",{
        "title":"18б · Селектор, обученный на библиотечных программах",
        "done":"Проверено переносимое двоичное правило на проектах, отсутствовавших в библиотечном обучении.",
        "summary":text,
        "paragraphs":[
            f"Обучение использовало {lock['training_programs']} ранее раскрытых библиотечных методов из {len(lock['training_projects'])} проектов. Порог и регуляризация выбраны с исключением целого проекта. При слабом CV основное правило выбирает постоянный C05. Модель и режим зафиксированы до записи новых проектов.",
            f"В новом тесте {count} методов из {out['families']} проектов, {r} повторов на настройку. Дополнительная сверка исходных манифестов исключила {len(excluded)} точных совпадений сеансов с обучением. Первичный расчёт по всем записям заменён результатом с этим исключением. Признаки из сигнатуры JVM и SMT-префикса извлечены до первой проверки, средняя стоимость {micros:.1f} мкс. Селектор выбирает между общим default и C05. Оракул выбирает на одной половине повторов и проверяется на другой.",
            f"Основное фиксированное правило выбрало C05 для {out['global_programs_policy']} методов, самостоятельная модель для {out['global_programs_raw_rule']}. 95% ДИ пересэмплирует целые проекты."
        ],
        "tables":[card_table(f"Двоичная маршрутизация · {count} классов, {count*r} воспроизведений на режим",
              ["Режим","PAR2, с","К C05 [95% ДИ]","К default [95% ДИ]","Таймауты"],
              [[k,f'{q["par2"]:.3f}',fmt_ci(q["to_global"]),fmt_ci(q["to_default"]),q.get("timeouts","—")]
               for k,q in stats.items()])],
        "paths":["protocols/library-trained-selector.json","protocols/library-selector-cross-source-dedup.json",
                 str((folder/"real-selector-metrics.json").relative_to(ROOT)),
                 str((folder/"VERIFY.json").relative_to(ROOT))],
        "recommendation":"Выбирать простой селектор только при выигрыше на целых ранее неиспользованных проектах и при пренебрежимой цене его признаков.",
        "next":"Повторить бинарный выбор на целевом живом клиенте, включая время подготовки и извлечения моделей."
    })
    build()
    return out
if __name__=="__main__":
    prepare()
