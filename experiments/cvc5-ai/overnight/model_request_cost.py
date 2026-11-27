"""Test how the fixed flags interact with SMT-LIB model retrieval requests."""
from collections import Counter, defaultdict
import os

import numpy as np

from arrays_options import SCOPE as PREVIOUS
from portfolio import frac, group_draws
from replay import CORES, ROOT, digest, ev, launch, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci, replay_cube
from validate_cores import cvc_capture, render, terms

BASE=ROOT/"runs/32-model-requests"
SCOPE=ROOT/"protocols/model-request-cost.json"
SOURCE=ROOT/"runs/23-rewrite-transfer/independent-manifest.json"
REPEATS=6
CAP=1.5


def prepare():
    if SCOPE.exists():
        return load(SCOPE)
    BASE.mkdir(exist_ok=True)
    raw=load(SOURCE)
    groups=defaultdict(list)
    for item in raw["sessions"]:
        if item["operations"].get("model",0)>=3:
            groups[item["family"]].append(item)
    sample=[item for family,entries in sorted(groups.items()) for item in sorted(entries,key=lambda q:q["sha256"])[:6]]
    assert len(sample)>=12
    original=[]
    omitted=[]
    variants=BASE/"without-models"
    variants.mkdir()
    for i,item in enumerate(sample):
        src=SOURCE.parent/item["file"]
        assert digest(src)==item["sha256"]
        tree=terms(src.read_text())
        cut=sum(t[0]=="get-model" for t in tree)
        assert cut==item["operations"]["model"] and cut>=3
        keep=[t for t in tree if t[0]!="get-model"]
        file=variants/f"{i:03d}.smt2"
        file.write_text("\n".join(render(t) for t in keep)+"\n")
        original.append({**item,"file":os.path.relpath(src,BASE)})
        omitted.append({**item,"file":str(file.relative_to(BASE)),"sha256":digest(file),"original_sha256":item["sha256"],
                        "get_model_requests_removed":cut})
    a,b=BASE/"full.json",BASE/"omit.json"
    for path,rows in ((a,original),(b,omitted)):
        write_json(path,{"sessions":rows,"group_count":len(rows),"scope":"reused full windows and model-request ablations"})
        ev.verify(path)
    rewrite=load(PREVIOUS)["configs"]["global_rewrite"]
    s={"locked":now(),"question":"Is the rewriting PAR2 gain stable after suppressing all get-model commands in the fixed recorded streams?",
       "original_sha256":digest(SOURCE),"manifest_hashes":{a.name:digest(a),b.name:digest(b)},
       "options":{"default/full":{},"rewrite/full":rewrite,"default/omit":{},"rewrite/omit":rewrite},
       "design":"Seeded paired interleaving of same fixed assertions and checks. Delete only model requests in the ablation with expected SAT/UNSAT and unsat core operations retained. Do not use the ablation as an end-to-end workload.",
       "sample":"Already observed projects, selected by source hash and at least three model operations, up to six methods per project.",
       "primary":"Interaction of differences relative to default, grouped by project. CAP=1.5, six repeats, common arrays-exp."}
    write_json(SCOPE,s)
    SCOPE.chmod(0o444)
    return s


def smoke(plan):
    folder=BASE/"smoke"
    if (folder/"VERIFY.json").exists():
        assert load(folder/"VERIFY.json")["ok"]
        return
    folder.mkdir()
    base=BASE/"full.json"
    name=min(load(base)["sessions"],key=lambda x:len(x["oracle"]))["group"]
    proof={}
    original=os.sched_getaffinity(0)
    try:
        os.sched_setaffinity(0,{CORES[0]})
        for label,params in plan["options"].items():
            arm,variant=label.split("/")
            source=BASE/(variant+".json")
            record=next(r for r in load(source)["sessions"] if r["group"]==name)
            tested=cvc_capture(source.parent/record["file"],params,True)
            (folder/(arm+"-"+variant+".stdout")).write_text(tested["stdout"])
            (folder/(arm+"-"+variant+".stderr")).write_text(tested["stderr"])
            proof[label]=tested["status"]=="solved" and ev.ANSWERS.findall(tested["stdout"])==record["oracle"]
    finally:
        os.sched_setaffinity(0,original)
    write_json(folder/"VERIFY.json",{"program":name,"ok":all(proof.values()),"cases":proof})
    assert all(proof.values())


def analyse(run):
    _,names,arms,S,T,W=replay_cube(run)
    meta={r["group"]:r for r in load(BASE/"full.json")["sessions"]}
    families=[meta[g]["family"] for g in names]
    draw=group_draws(families,np.random.default_rng(1586431))
    v={a:S[:,i,:].mean(axis=1) for i,a in enumerate(arms)}
    difference=(v["rewrite/omit"]-v["default/omit"])-(v["rewrite/full"]-v["default/full"])
    d=[float(difference.mean()),np.percentile((draw@difference)/draw.sum(axis=1),[2.5,97.5]).tolist()]
    summary={a:{"par2":float(z.mean()),"ratio":frac(z,v["default/"+a.split("/")[1]],draw),
                "counts":Counter(T[:,arms.index(a),:].ravel())} for a,z in v.items()}
    result={"programs":len(names),"projects":len(set(families)),"repeats":REPEATS,
            "model_requests_per_traversal":sum(r["operations"]["model"] for r in meta.values()),
            "count":int(np.prod(S.shape)),"interaction_s":d,"modes":summary,"scope_sha256":digest(SCOPE)}
    write_json(run/"metrics.json",result)
    intro=f"Удаление запросов модели изменило разность PAR2 переписывание–default на {1000*d[0]:+.1f} мс [{1000*d[1][0]:+.1f}; {1000*d[1][1]:+.1f}]."
    write_json(ROOT/"evidence/32-model-requests.json",{
        "title":"32 · Роль запросов модели в эффекте переписывания",
        "done":f"Проверены {result['count']} запусков с запросами моделей и без них.",
        "summary":intro,
        "paragraphs":[
            f"Повторно использованы {len(names)} методов из {result['projects']} проектов, по {REPEATS} повторов. В среднем по обходу исключено {result['model_requests_per_traversal']} запросов get-model. Исходный режим сохраняет полный поток.",
            "В диагностическом варианте исключён только get-model. Сохраняются утверждения, порядок проверок, исходные ответы и запросы ядер. Число успехов и таймаутов измерено заново при одинаковом лимите.",
            "Изменение включает затраты на построение, вывод и возможные последствия запроса для внутреннего состояния. Интервал пересэмплирует проекты. Сеансы без моделей — контрфактическая механистическая проверка.",
        ],
        "tables":[card_table(f"По {len(names)*REPEATS} попыток на каждое условие",
                    ["Условие","PAR2, с","К default в том же режиме [95% ДИ]","Таймауты","Ошибки","Unknown","Несовпадения"],
                    [[a,f"{q['par2']:.3f}",fmt_ci(q["ratio"]),*[q["counts"].get(k,0) for k in ("timeout","error","unknown","incorrect")]] for a,q in summary.items()])],
        "paths":["protocols/model-request-cost.json","runs/32-model-requests/smoke/VERIFY.json",
                 "runs/32-model-requests/replay/VERIFY.json","runs/32-model-requests/replay/metrics.json"],
        "recommendation":"На живом клиенте учитывать реальные затраты извлечения модели при переносе эффекта CLI.",
        "next":"Сопоставить результат с целевой трассой, где модели читаются и используются в исследовании путей.",
    })
    build()


def main():
    s=prepare()
    smoke(s)
    full,omit=BASE/"full.json",BASE/"omit.json"
    assert all(digest(BASE/name)==fingerprint for name,fingerprint in s["manifest_hashes"].items())
    run=BASE/"replay"
    if not (run/"VERIFY.json").exists():
        launch(run,full,s["options"],REPEATS,401319,380,timeout=CAP,
               manifest_by_arm={a:omit for a in s["options"] if a.endswith("/omit")},
               extra_flags_by_arm={a:["--arrays-exp"] for a in s["options"]})
    analyse(run)


if __name__=="__main__":
    main()
