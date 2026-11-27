"""Test pre-check KSMT prefix descriptors as a portable program selector."""
from collections import Counter
import base64
import hashlib
from pathlib import Path
import re
import time

import numpy as np

from corpus import materialize
from portfolio import BASE as PORT, MENU, LOCK, choose, features, fit, frac, group_draws, predict
from replay import CORES, ROOT, digest, launch, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE=ROOT/"runs/08-prefix"
SCOPE=ROOT/"protocols/prefix-selector.json"
DEV=PORT/"dev-corpus/manifest.json"
REPEATS=6
OPERATORS=(b"bvmul",b"bvadd",b"bvsrem",b"bvurem",b"bvudiv",b"bvsdiv",b"bvashr",
           b"bvlshr",b"bvshl",b"bvand",b"bvxor",b"ite",b"select",b"store",b"extract")

def prefix(path):
    n, pushes, assertions, assumptions, fields = 0,0,0,0,[]
    before=hashlib.sha256()
    with path.open("rb") as stream:
        for line in stream:
            op, arg, encoded=line.rstrip(b"\n").split(b"\t",2)
            if op.startswith(b"check"):
                break
            before.update(line)
            n+=1
            pushes+=op==b"push"
            assertions+=op==b"assert"
            assumptions+=op==b"assumption"
            if op in (b"assert",b"assumption"):
                fields.append(base64.b64decode(encoded))
        else:
            raise ValueError("no first check in archived trace")
    concatenated=b"\n".join(fields)
    tokens=Counter(re.findall(rb"\(([a-z0-9_-]+)\b",concatenated))
    return ([n,pushes,assertions,assumptions,len(concatenated),concatenated.count(b"("),
             tokens[b"declare-fun"]+tokens[b"declare-const"],*[tokens[v] for v in OPERATORS]],before.hexdigest())

def load_design(manifest,groups,variant):
    m=load(manifest)
    rows={a["group"]:a for a in m["sessions"]}
    src=m["sessions"][0].get("source_sha256") is not None and (manifest.parent/"programs.json").exists()
    sources={r["sha256"]:r for r in load(manifest.parent/"programs.json")["programs"]} if src else {}
    x,families,hashes=[],[],{}
    tick=time.perf_counter_ns()
    for g in groups:
        rec=rows[g]
        vector=[]
        if variant in ("prefix","combined"):
            vector,prefix_hash=prefix(manifest.parent/rec["trace"])
            # Identity of the byte prefix and the trace itself is auditable.
            hashes[g]=prefix_hash
        if variant in ("source","combined"):
            vector.extend(features(manifest.parent/sources[rec["source_sha256"]]["source"]))
        x.append(vector)
        families.append(rec["family"])
    us=(time.perf_counter_ns()-tick)/1e3/len(groups)
    return np.asarray(x,dtype=float),np.array(families),hashes,us

def learn():
    if SCOPE.exists():
        return load(SCOPE)
    menu=load(MENU)
    saved=load(LOCK)
    _,groups,arms,score,status,_=replay_cube(PORT/"dev-replay")
    eligible=saved["eligible"]
    assert "default" in eligible
    inc=eligible.index(menu["global_arm"])
    y=score[:,[arms.index(k) for k in eligible]].mean(axis=2)
    protocols={}
    for variant in ("prefix","source","combined"):
        x,families,hashes,us=load_design(DEV,groups,variant)
        traces=[]
        for lam in (4.,16.,64.):
            pred=np.zeros_like(y)
            for family in set(families):
                target=families==family
                pred[target]=predict(fit(x[~target],y[~target],lam),x[target])
            for gate in (0.,.04,.12,.20):
                sel=choose(pred,inc,gate)
                costs=y[np.arange(len(y)),sel]
                traces.append({"l2":lam,"gate_s":gate,"par2":float(costs.mean()),
                               "switches":int(np.sum(sel!=inc)),"choices":[eligible[i] for i in sel]})
        best=min(traces,key=lambda r:(r["par2"],-r["l2"],-r["gate_s"]))
        protocols[variant]={"model":fit(x,y,best["l2"]),"gate_s":best["gate_s"],"cv_par2":best["par2"],
                            "cv_candidates":traces,"dev_prefixes":hashes,"feature_us":us}
    best=min(protocols,key=lambda name:(protocols[name]["cv_par2"],name))
    inc_par=float(y[:,inc].mean())
    lock={"locked":now(),"why":"Distinguish lack of static routing signal from scope mismatch: prefix shape immediately before first solver check versus Java source features.",
          "dev_existing":"portfolio-dev only; test and transfer outcomes excluded from model fits and gate selection",
          "locked_menu_sha256":digest(MENU),"source_selector_lock_sha256":digest(LOCK),
          "train_file_sha256":digest(DEV),"train_metrics_sha256":digest(PORT/"dev-replay/metrics.json"),
          "config_set":eligible,"global":menu["global_arm"],"configs":menu["arms"],"variants":protocols,
          "main_selector":"global" if protocols[best]["cv_par2"]>=inc_par-.010 else best,
          "cv_global":inc_par,"chosen_cv_type":best,
          "prefix_features":["operations","pushes","asserts","assumptions","encoded_bytes","open_parentheses","declarations",
                             *[o.decode() for o in OPERATORS]],
          "independence":"consume event rows preceding first check exclusively. No check reply, timing, remaining trace, later model or core calls used.",
          "evaluation":"heldout family-disjoint, default, frozen global, selected policy, three fixed variant contrasts, cross-half per-program oracle"}
    write_json(SCOPE,lock)
    SCOPE.chmod(0o444)
    return lock

def analyse(path,manifest,lock):
    n_repeats=load(path/"VERIFY.json")["repeats"]
    _,groups,arms,score,st,wall=replay_cube(path)
    data=load(manifest)
    n=len(groups)
    h=n_repeats//2
    configs=lock["config_set"]
    positions=[arms.index(k) for k in configs]
    global_arm=lock["global"]
    gl=arms.index(global_arm)
    J=np.arange(n)
    code_index=configs.index(global_arm)
    selections={"default":np.full(n,arms.index("default")),"global":np.full(n,gl)}
    prefix_hashes={}
    compute_us={}
    for name,variant in lock["variants"].items():
        x,f,q,us=load_design(manifest,groups,name)
        if q:
            if prefix_hashes:
                assert q==prefix_hashes
            prefix_hashes=q
        scores=predict(variant["model"],x)
        selected=choose(scores,code_index,variant["gate_s"])
        selections[name]=np.asarray(positions)[selected]
        compute_us[name]=us
    deployment=lock["main_selector"]
    selections["primary"]=selections[deployment]
    m=score.mean(axis=2)
    outcomes={k:m[J,v] for k,v in selections.items()}
    ft=score[:,:,:h].mean(axis=2)
    bt=score[:,:,h:].mean(axis=2)
    pick_front=np.asarray(positions)[np.argmin(ft[:,positions],axis=1)]
    pick_back=np.asarray(positions)[np.argmin(bt[:,positions],axis=1)]
    outcomes["oracle"]=(ft[J,pick_back]+bt[J,pick_front])/2
    status={k:Counter(st[J,v,:].flatten()) for k,v in selections.items()}
    status["oracle"]=Counter(np.concatenate([st[J,pick_back,:h],st[J,pick_front,h:]],axis=1).flatten())
    bootstrap=group_draws(f,np.random.default_rng(148150))
    stats={k:{"par2":float(v.mean()),"vs_global":frac(v,outcomes["global"],bootstrap),
              "vs_default":frac(v,outcomes["default"],bootstrap),"counts":status[k],
              "switches":int(np.sum(selections[k]!=gl)) if k in selections else None}
           for k,v in outcomes.items()}
    result={"programs":n,"families":len(set(f)),"repeats":n_repeats,"rejected":len(data["rejected"]),
            "measurements":load(path/"VERIFY.json")["rows"],"stats":stats,"mode":deployment,"feature_us":compute_us,
            "prefixes_verified":prefix_hashes,
            "per_program":{g:{"family":f[i],"choices":{k:arms[sel[i]] for k,sel in selections.items()},
                "par2":{k:float(sol[i]) for k,sol in outcomes.items()}} for i,g in enumerate(groups)}}
    write_json(path/"metrics.json",result)
    label={"default":"default","global":"Фиксированный глобальный","source":"Java-исходник",
           "prefix":"Префикс SMT","combined":"Префикс и Java","primary":"Зафиксированное правило",
           "oracle":"Оракул, независимые повторы"}
    s1=stats["source"]["vs_global"]
    s2=stats["prefix"]["vs_global"]
    text=(f"Признаки до первой проверки: отношение к глобальному {fmt_ci(s2)} у префикса SMT "
          f"и {fmt_ci(s1)} у признаков Java на {n} новых программах.")
    write_json(ROOT/"evidence/09-prefix.json",{
        "title":"09 · Селектор по префиксу до первой проверки",
        "done":f"На {n} новых программах проверены три простых заранее зафиксированных селектора.",
        "summary":text,
        "paragraphs":[
            f"Новый закрытый набор с раздельными семействами, {n} программ в {len(set(f))} семействах. {n_repeats} повторов на ячейку. Обучающая выборка та же разработка портфеля, использовано CV по семействам. Все три модели и основное правило {deployment} зафиксированы до открытия seed.",
            "Ridge на префиксе видит только операции и SMT-термы, которые уже переданы решателю до первого check. Признаки включают размер текста, операции с битовыми векторами, массивы и число предположений. Контроль Java читает исходный файл. Прочитанный байтовый префикс проверяется по хешу. Выбор до проверки моделируется на записанной нагрузке.",
            f"Извлечение префикса в среднем {compute_us['prefix']:.1f} мкс на сеанс. Оракул повторяет выбор и оценку на разных половинах повторов. Доверительные интервалы пересэмплируют целые семейства ({B} выборок). Из порядка сеансов исключено {len(data['rejected'])} непринятых записей."
        ],
        "tables":[card_table(f"Выбор конфигурации на свежем тесте · {n} программ, по {n*n_repeats} сеансов на строку",
                  ["Режим","PAR2, с","К глобальному [95% ДИ]","К default [95% ДИ]","Таймауты",
                   "Ошибки + unknown + несовпадения","Программ с переключением"],
                  [[label[k],f'{v["par2"]:.3f}',fmt_ci(v["vs_global"]),fmt_ci(v["vs_default"]),v["counts"].get("timeout",0),
                    sum(v["counts"].get(s,0) for s in ("error","incorrect","unknown")),
                    v["switches"] if v["switches"] is not None else "—"] for k,v in stats.items()])],
        "paths":["protocols/prefix-selector.json","runs/08-prefix/corpus/manifest.json",
                 "runs/08-prefix/replay/metrics.json","runs/08-prefix/replay/VERIFY.json",
                 "runs/08-prefix/replay/worker-{0..5}.jsonl"],
        "recommendation":"Использовать простое правило выбора только после измеримого преимущества на независимых семействах. Если выигрыш сосредоточен у оракула, отдельно проверить переключение по исчерпанию части бюджета.",
        "next":"Проверить бюджетное переключение между двумя конфигурациями с реальным перезапуском полного потока."
    })
    build()

def main():
    s=learn()
    build("Проверяю признаки SMT-префикса, доступные до первой проверки, на отдельных закрытых семействах.")
    path=BASE/"corpus"
    m=path/"manifest.json"
    if not m.exists():
        priors=[PORT/"dev-corpus",PORT/"test-corpus",ROOT/"runs/05-search/heldout",
                ROOT/"runs/06-cap-calibration/corpus",ROOT/"runs/07-solver-seeds/corpus"]
        m=materialize("prefix-test",SCOPE,path,priors,core=CORES[0])
    out=BASE/"replay"
    if not (out/"VERIFY.json").exists():
        launch(out,m,s["configs"],REPEATS,70110,2200)
    analyse(out,m,s)
if __name__=="__main__":
    main()
