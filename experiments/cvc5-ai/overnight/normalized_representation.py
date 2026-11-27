"""Retest the controlled representation contrast after enabling constant arrays uniformly."""
from collections import Counter

import numpy as np

from portfolio import frac, group_draws
from replay import CORES, ROOT, digest, ev, launch, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE=ROOT/"runs/10-representation"
SCOPE=ROOT/"protocols/representation-arrays-exp.json"
REPLAY=BASE/"with-arrays-exp"
REPEATS=8

def scope():
    if SCOPE.exists():
        return load(SCOPE)
    first=load(BASE/"replay/VERIFY.json")
    m=load(BASE/"corpus/manifest.json")
    configs=load(ROOT/"protocols/representation.json")["configs"]
    n=sum(r["representation"]=="array" for r in m["sessions"])
    errors=sum(c.get("error",0) for c in first["counts"].values())
    assert errors==n*first["repeats"]*len(configs)
    s={"locked":now(),"reason":"Every array variant hit STORE_ALL under every original config. Determine actual timing once the common CLI feature is enabled.",
       "manifest_sha256":digest(BASE/"corpus/manifest.json"),"raw_verify_sha256":digest(BASE/"replay/VERIFY.json"),
       "original_affected":n,"original_errors":errors,"configs":configs,"repeats":REPEATS,
       "shared_flag":"--arrays-exp","comparison":"same paired source families and original solver menu with one switch shared by all arms, verify answers again; this is a repaired contrast on reused recordings"}
    write_json(SCOPE,s)
    SCOPE.chmod(0o444)
    return s

def smoke(config,manifest):
    target=BASE/"arrays-exp-smoke.json"
    if target.exists():
        assert load(target)["ok"]
        return
    m=load(manifest)
    shortest=sorted([r for r in m["sessions"] if r["representation"]=="array"],key=lambda x:len(x["oracle"]))[:2]
    import os
    cur=os.sched_getaffinity(0)
    flags=ev.flags
    outcomes=[]
    try:
        os.sched_setaffinity(0,{CORES[0]})
        ev.flags=lambda p: [*flags(p),"--arrays-exp"]
        for r in shortest:
            for name,v in config["configs"].items():
                outcomes.append({"arm":name,**ev.measure(manifest,r,v,1.5)})
    finally:
        ev.flags=flags
        os.sched_setaffinity(0,cur)
    ok=all(r["status"] not in ("error","unknown","incorrect") for r in outcomes)
    write_json(target,{"ok":ok,"programs":len(shortest),"repeats":1,"outcomes":outcomes})
    assert ok

def publish(path,manifest,s):
    m=load(manifest)
    v=load(path/"VERIFY.json")
    _,groups,arms,score,status,wall=replay_cube(path)
    info={r["group"]:r for r in m["sessions"]}
    families=sorted({x["family"] for x in info.values()})
    loc={(info[g]["family"],info[g]["representation"],info[g]["width"]):i for i,g in enumerate(groups)}
    summ=score.mean(axis=2)
    d=arms.index("default")
    obs={}
    tab=[]
    rng=np.random.default_rng(287000)
    draws=rng.integers(len(families),size=(B,len(families)))
    for j,n in enumerate(arms):
        per_flag={}
        series=[]
        for W in (7,12):
            base=[loc[(f,"branch",W)] for f in families]
            array=[loc[(f,"array",W)] for f in families]
            diff=(summ[array,j]-summ[array,d])-(summ[base,j]-summ[base,d])
            series.append(diff)
            per_flag[str(W)]={"value_s":float(diff.mean()),"ci":np.percentile(diff[draws].mean(axis=1),[2.5,97.5]).tolist()}
            for kind,rows in (("branch",base),("array",array)):
                mat=group_draws(families,np.random.default_rng(98210))
                ctr=Counter(status[rows,j].ravel())
                tab.append([n,W,kind,len(rows),f"{summ[rows,j].mean():.3f}",
                            fmt_ci(frac(summ[rows,j],summ[rows,d],mat)),
                            ctr["timeout"],ctr["error"],ctr["incorrect"],ctr["unknown"]])
        combined=np.mean(series,axis=0)
        per_flag["combined"]={"value_s":float(combined.mean()),
                              "ci":np.percentile(combined[draws].mean(axis=1),[2.5,97.5]).tolist()}
        obs[n]=per_flag
    array_ids=[i for i,g in enumerate(groups) if info[g]["representation"]=="array"]
    ct=Counter(status[array_ids].ravel())
    result={"programs":len(groups),"complete_families":len(families),"repeats":v["repeats"],"rows":v["rows"],
            "array_programs":len(array_ids),"array_outcomes":ct,"contrasts":obs,"all_counts":v["counts"]}
    write_json(path/"metrics.json",result)
    q=obs["global"]["combined"]
    text=f"После общего --arrays-exp эффект замены ветвления массивом для C05 составил {q['value_s']*1000:+.1f} мс PAR2."
    write_json(ROOT/"evidence/14b-representation-arrays-exp.json",{
        "title":"14б · Представление массива после исправления поддержки",
        "done":"Повторно измерен контролируемый контраст без систематического штрафа исходного режима.",
        "summary":text,
        "paragraphs":[
            f"Те же {len(groups)} программ в {len(families)} полных семействах. Каждая настройка, включая default и его дубль, получает --arrays-exp. {v['repeats']} повторов на ячейку, {v['rows']} сеансов. Порядок заново рандомизирован.",
            f"В исходной серии были {s['original_errors']} ошибок STORE_ALL. Сейчас для массива зарегистрировано {ct['solved']} полных, {ct['timeout']} таймаутов, {ct['error']} ошибок, {ct['unknown']} unknown и {ct['incorrect']} несовпадений из {sum(ct.values())}.",
            f"Разница эффекта C05 между массивом и ветвлением {q['value_s']*1000:+.1f} мс, 95% ДИ [{q['ci'][0]*1000:+.1f}; {q['ci'][1]*1000:+.1f}] мс. Пересэмплирование по полному семейству. Это перенос внутри известных записей и отдельное влияние общей поддержки массивов."
        ],
        "tables":[card_table("Одинаковая поддержка массивов для всех настроек",
                   ["Флаги","Входных битов","Представление","Программ","PAR2, с","К default [95% ДИ]",
                    "Таймауты","Ошибки","Несовпадения","Unknown"],tab)],
        "paths":["protocols/representation-arrays-exp.json","runs/10-representation/arrays-exp-smoke.json",
                 "runs/10-representation/with-arrays-exp/VERIFY.json",
                 "runs/10-representation/with-arrays-exp/metrics.json"],
        "recommendation":"Для сравнения форм Java сначала фиксировать общий режим разрешённых операций SMT-LIB.",
        "next":"Сопоставить скорректированный контраст с независимо записанными библиотеками и живым клиентом."
    })
    build()

def main():
    s=scope()
    manifest=BASE/"corpus/manifest.json"
    smoke(s,manifest)
    if not (REPLAY/"VERIFY.json").exists():
        flags={k:["--arrays-exp"] for k in s["configs"]}
        launch(REPLAY,manifest,s["configs"],REPEATS,608188,1350,extra_flags_by_arm=flags)
    publish(REPLAY,manifest,s)
if __name__=="__main__":
    main()
