"""Confirm a single rewriting switch on untouched cached project families."""
from collections import Counter
import sys

import numpy as np

from arrays_options import SCOPE as ARRAY_SCOPE
from bridge import independent_single
from portfolio import frac, group_draws
from replay import ROOT, digest, launch, load, now
from report import build, write_json
import rewrite_transfer
from stability_metrics import card_table, fmt_ci, replay_cube
from transfer import capture

BASE=ROOT/"runs/31-minimal-transfer"
SCOPE=ROOT/"protocols/minimal-rewrite-transfer.json"
REPEATS=6
ARTIFACTS=(
    ("commons-csv","org/apache/commons","1.12.0","org.apache.commons.csv.",14),
    ("commons-beanutils","commons-beanutils","1.9.4","org.apache.commons.beanutils.",17),
    ("joda-time","joda-time","2.12.7","org.joda.time.field.",17),
    ("xz","org/tukaani","1.9","org.tukaani.xz.",17),
    ("snakeyaml","org/yaml","2.2","org.yaml.snakeyaml.nodes.",14),
)
PRIOR=(*rewrite_transfer.PRIOR,rewrite_transfer.BASE/"independent-manifest.json")


def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    previous=load(ARRAY_SCOPE)
    artifacts=[]
    for lib,group,ver,package,n in ARTIFACTS:
        file=rewrite_transfer.MAVEN/group/lib/ver/f"{lib}-{ver}.jar"
        assert file.is_file()
        artifacts.append({"project":lib,"jar":str(file),"sha256":digest(file),"package":package,
                          "max_classes":n,"method_ms":6500,"solver_ms":1900})
    cfg={"default":{},"default_alias":{},"rewrite":previous["configs"]["global_rewrite"],
         "simple":{"simplification":"none"}}
    s={"locked":now(),"why":"Determine whether the one option reproduces the full fixed rewrite vector on new project families.",
       "start_gate":"Single-option/simple-to-full point ratio <=1.05 and simple-to-default <=0.97 in the diagnostic matrix.",
       "artifacts":artifacts,"configs":cfg,"common_flags":["--arrays-exp"],"array_bridge":previous["array_bridge"],
       "minimum_admitted_total":14,"minimum_admitted_projects":3,"repeats":REPEATS,
       "primary":"Simple to default and full rewrite at the same 1.5s cap; whole-project bootstrap on fresh family-held-out data.",
       "admission":"Independent Z3 on full ordered traces and cross-manifest exact deduplication. No cvc5 outcome used to select sealed methods."}
    write_json(SCOPE,s)
    SCOPE.chmod(0o444)
    return s


def report(path, source, extra):
    _,groups,arms,values,status,_=replay_cube(path)
    related={r["group"]:r["family"] for r in load(source)["sessions"]}
    fam=[related[g] for g in groups]
    draw=group_draws(fam,np.random.default_rng(721028))
    vals=values.mean(axis=2)
    default=vals[:,arms.index("default")]
    rewrite=vals[:,arms.index("rewrite")]
    effect={arm:{"par2":float(vals[:,i].mean()),"default":frac(vals[:,i],default,draw),
                 "rewrite":frac(vals[:,i],rewrite,draw),"counts":Counter(status[:,i,:].ravel())}
            for i,arm in enumerate(arms)}
    n=len(groups)
    outcome={"programs":n,"projects":len(set(fam)),"repeats":REPEATS,"rows":int(np.prod(values.shape)),
             "effects":effect,"clock_scope_sha256":digest(SCOPE),**extra}
    write_json(path/"metrics.json",outcome)
    q=effect["simple"]
    kind="новых" if outcome["fresh"] else "ранее записанных"
    intro=f"На {n} {kind} методах из {outcome['projects']} проектов simplification=none дал {fmt_ci(q['default'])} к default и {fmt_ci(q['rewrite'])} к трём флагам."
    write_json(ROOT/"evidence/31-minimal-transfer.json",{
        "title":"31 · Проверка одного флага переписывания на других библиотеках",
        "summary":intro,"done":f"Проверен один флаг переписывания на {n} библиотечных методах.",
        "paragraphs":[
            f"{n} {kind} методов, {outcome['projects']} проектных групп, {REPEATS} повторов. Векторы и источники были зафиксированы заранее, варианты перемешаны. Везде общий --arrays-exp и предел полного сеанса 1,5 с.",
            (f"Исключено {outcome['dedup']} совпадений с прежними сеансами и {outcome['adapter_rejected']} записей, которые не прошли нормализацию массивов." if outcome["fresh"] else
             "Из-за оставшегося времени использованы прежние переносимые записи из восьми проектов, отличные от выборки разложения 29. Однофлаговый вектор на этих потоках измеряется впервые. Общий дубликат двух источников исключён при сборке манифеста ранее."),
            "Доверительные интервалы пересэмплируют целые проекты."
        ],
        "tables":[card_table(f"На режим {n*REPEATS} сеансов",
                    ["Режим","PAR2, с","К default [95% ДИ]","К трём флагам [95% ДИ]",
                     "Таймауты","Ошибки","Unknown","Несовпадения"],
                    [[a,f"{s['par2']:.3f}",fmt_ci(s["default"]),fmt_ci(s["rewrite"]),
                      *[s["counts"].get(k,0) for k in ("timeout","error","unknown","incorrect")]] for a,s in effect.items()])],
        "paths":["protocols/minimal-rewrite-transfer.json",str(source.relative_to(ROOT)),
                 "runs/31-minimal-transfer/replay/VERIFY.json","runs/31-minimal-transfer/replay/metrics.json",
                 "runs/29-simplification/smoke/VERIFY.json"],
        "recommendation":"Если один параметр воспроизводит полезный эффект, предпочесть его в ограниченной проверке на целевом клиенте.",
        "next":"Сравнить его со штатным default на новых живых проектах при сопоставимой полноте завершения.",
    })
    build()


def main(fresh=True):
    plan=freeze()
    small=ROOT/"runs/29-simplification/smoke/VERIFY.json"
    assert load(small)["ok"]
    if fresh:
        assert not set(x["project"] for x in plan["artifacts"]).intersection(
            row["family"] for p in PRIOR for row in load(p)["sessions"])
        meta={**plan,"prior_manifests":{str(p.relative_to(ROOT)):digest(p) for p in PRIOR}}
        raw=BASE/"corpus/manifest.json"
        if not raw.exists():
            raw=capture(raw.parent,plan,SCOPE)
        converted=BASE/"portable/manifest.json"
        if not converted.exists():
            converted=independent_single(raw,converted.parent,plan["array_bridge"])
        rewrite_transfer.BASE=BASE
        source=rewrite_transfer.independent_manifest(converted,meta)
    else:
        source=ROOT/"runs/22-library-caps/manifest.json"
        assert digest(source)==load(ROOT/"protocols/library-caps.json")["manifest_sha256"]
    data=load(source)
    assert data["group_count"]>=plan["minimum_admitted_total"]
    assert len({r["family"] for r in data["sessions"]})>=plan["minimum_admitted_projects"]
    path=BASE/"replay"
    if not (path/"VERIFY.json").exists():
        launch(path,source,plan["configs"],REPEATS,2155324,585 if fresh else 435,
               extra_flags_by_arm={a:plan["common_flags"] for a in plan["configs"]})
    report(path,source,{"fresh":fresh,"dedup":len(data.get("prior_exact_duplicates",[])),
                        "adapter_rejected":len(data.get("adapter_excluded",[])),"smoke_sha256":digest(small)})


if __name__=="__main__":
    main("--reuse" not in sys.argv)
