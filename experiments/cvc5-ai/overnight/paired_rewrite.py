"""Test rewriting on new matched branch and table forms of the same Java programs."""
from collections import Counter
import os
import sys

import numpy as np

from arrays_options import SCOPE as ARRAYS
from corpus import seal
from portfolio import frac, group_draws
import representation
from replay import CORES, ROOT, digest, ev, launch, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci, replay_cube
from validate_cores import cvc_capture

BASE = ROOT / "runs/30-paired-rewrite"
SCOPE = ROOT / "protocols/paired-rewrite.json"
SPLIT = "paired-rewrite-test"
REPEATS = 8
CAP = 1.5


def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    commit = seal(SPLIT, 20, 4, 32, method_ms=7500, solver_ms=2200)
    scope = load(ARRAYS)
    design = {
        "locked": now(), "seed_sha256": commit["seed_commitment_sha256"],
        "options": {"default": {}, "C05": scope["configs"]["global"],
                    "rewrite": scope["configs"]["global_rewrite"], "default_alias": {}},
        "minimum_complete_families": 8, "repeats": REPEATS, "timeout_s": CAP,
        "purpose": "Does the fixed library rewriting subset transfer to generated branches or portable indexed-table versions with equivalent Java values, on new families?",
        "primary": "Paired contrast of rewrite minus default PAR2 for array minus branch, same original semantics and matched mask widths. Bootstrap independent base families.",
        "table_control": "Common arrays-exp fixed, same settings and caps in both representations. Show both mask widths as sensitivities. Quality gate uses only Z3 and session completeness.",
        "scope": "20 new Java base families, retain only complete four-cell families. One base generating two representations and two bit-width settings. No model sees this test.",
        "heldout_exclusions": "All previous prepared generator rosters and original frozen sources.",
    }
    write_json(SCOPE, design)
    SCOPE.chmod(0o444)
    return design


def smoke(plan):
    path = BASE / "smoke"
    if (path/"VERIFY.json").exists():
        assert load(path/"VERIFY.json")["ok"]
        return
    path.mkdir()
    source = ROOT/"runs/10-representation/corpus/manifest.json"
    samples = {mode:min((r for r in load(source)["sessions"] if r["representation"] == mode),
                        key=lambda item:(len(item["oracle"]),item["sha256"]))
               for mode in ("branch","array")}
    original = os.sched_getaffinity(0)
    receipts = {}
    try:
        os.sched_setaffinity(0,{CORES[0]})
        for arm, options in plan["options"].items():
            arms=[]
            for mode, sample in samples.items():
                file=source.parent/sample["file"]
                assert digest(file)==sample["sha256"]
                tested=cvc_capture(file,options,True)
                (path/f"{arm}-{mode}.stdout").write_text(tested["stdout"])
                (path/f"{arm}-{mode}.stderr").write_text(tested["stderr"])
                exact=tested["status"]=="solved" and ev.ANSWERS.findall(tested["stdout"])==sample["oracle"]
                arms.append({"mode":mode,"program":sample["group"],"status":tested["status"],"exact":exact})
            receipts[arm]=arms
    finally:
        os.sched_setaffinity(0,original)
    ok=all(row["exact"] for results in receipts.values() for row in results)
    write_json(path/"VERIFY.json",{"ok":ok,"cases":receipts})
    assert ok


def analyse(run, manifest, fresh):
    _,groups,arms,scores,status,_=replay_cube(run)
    meta=load(manifest)
    features={r["group"]:r for r in meta["sessions"]}
    families=sorted({features[g]["family"] for g in groups})
    assert len(groups)==4*len(families)
    draws=group_draws(families,np.random.default_rng(1800647))
    means=scores.mean(axis=2)
    mapping={(features[g]["family"],features[g]["width"],features[g]["representation"]):i for i,g in enumerate(groups)}
    effects={}
    widths=(7,12)
    for width in ("all", *widths):
        cell={}
        use=widths if width=="all" else (width,)
        for rep in ("branch","array"):
            indices=np.array([[mapping[family,w,rep] for w in use] for family in families])
            vals=means[indices].mean(axis=1)
            counters={arm:Counter(status[indices,idx,:].ravel()) for idx,arm in enumerate(arms)}
            stats={arm:{"ratio":frac(vals[:,idx],vals[:,arms.index("default")],draws),
                        "par2":float(vals[:,idx].mean()),"counts":counters[arm]}
                   for idx,arm in enumerate(arms)}
            cell[rep]={"scores":vals,"by_arm":stats,"programs":len(families)*len(use)}
        diffs={}
        a,b=(cell[mode]["scores"] for mode in ("array","branch"))
        anchor=arms.index("default")
        for cfg in ("C05","rewrite"):
            idx=arms.index(cfg)
            difference=(a[:,idx]-a[:,anchor])-(b[:,idx]-b[:,anchor])
            ci=np.percentile((draws@difference)/draws.sum(axis=1),[2.5,97.5]).tolist()
            diffs[cfg]=[float(difference.mean()),ci]
        effects[str(width)]={"interaction_s":diffs,
            **{rep:{key:value for key,value in cell[rep].items() if key!="scores"} for rep in ("array","branch")}}
    result={"programs":len(groups),"families":len(families),"repeats":REPEATS,"fresh":fresh,
            "rows":int(np.prod(scores.shape)),"omitted_count":meta["planned_programs"]-len(groups),
            "cases":effects,"source_manifest":str(manifest.relative_to(ROOT)),
            "source_manifest_sha256":digest(manifest),"scope_sha256":digest(SCOPE)}
    write_json(run/"metrics.json",result)
    return result


def publish(m):
    n,f=m["programs"],m["families"]
    pooled=m["cases"]["all"]
    d,(lo,hi)=pooled["interaction_s"]["rewrite"]
    kind="новых" if m["fresh"] else "ранее записанных"
    text=f"На {f} {kind} парных семействах отличие эффекта переписывания «таблица минус ветвление» составило {1000*d:+.1f} мс PAR2 [{1000*lo:+.1f}; {1000*hi:+.1f}]."
    rows=[]
    for width, cases in m["cases"].items():
        for representation in ("branch","array"):
            data=cases[representation]
            for arm,q in data["by_arm"].items():
                rows.append([width,representation,arm,data["programs"]*REPEATS,f'{q["par2"]:.3f}',fmt_ci(q["ratio"]),
                             *[q["counts"].get(x,0) for x in ("timeout","error","unknown","incorrect")]])
    write_json(ROOT/"evidence/30-paired-rewrite.json",{
        "title":"30 · Перенос переписывания между таблицей и ветвлением",
        "done":f"Сверены {m['rows']} запусков двух представлений и масок.",
        "summary":text,
        "paragraphs":[
            f"{n} Java-программ от {f} {kind} базовых семейств, по {REPEATS} повторов на режим. Семейство содержит эквивалентные вычисления на ветвлении и индексируемой таблице с масками 7 и 12 бит. При неполном семействе действовал заранее заданный отсев по Z3 и пригодности трассы. Отсеяно {m['omitted_count']} программ из запланированных.",
            "Точные варианты C05 и переписывания взяты из библиотечного опыта. Использован общий --arrays-exp. Варианты сравнивались в случайном порядке внутри каждого сеанса. Взаимодействие сравнивает разность PAR2 к default между представлениями внутри семейства. Интервал пересэмплирует целые базовые семейства.",
            "Если метод содержит несколько полностью совпадающих проверок, они входят в один сеанс. Маски и представления одной базы также остаются в одном независимом семействе."
        ],
        "tables":[card_table("Парные полные сеансы · знаменатель на строку и режим",
                    ["Маска","Форма","Режим","Сеансов","PAR2, с","К default [95% ДИ]",
                     "Таймаут","Ошибки","Unknown","Несовпадения"],rows)],
        "paths":["protocols/paired-rewrite.json",m["source_manifest"],
                 "runs/30-paired-rewrite/replay/VERIFY.json","runs/30-paired-rewrite/replay/metrics.json",
                 "runs/30-paired-rewrite/replay/worker-{0..5}.jsonl"],
        "recommendation":"Привязывать перенос выигравших параметров к проверенному распределению целевых проектов и представлению массивов.",
        "next":"Воспроизвести наблюдаемый контраст на живом клиенте с той же версией и настройками cvc5.",
    })
    build()


def main(fresh=True):
    BASE.mkdir(exist_ok=True)
    plan=freeze()
    smoke(plan)
    representation.BASE=BASE
    representation.SCOPE=SCOPE
    representation.SPLIT=SPLIT
    m=(BASE/"corpus/manifest.json") if fresh else (ROOT/"runs/10-representation/corpus/manifest.json")
    if fresh and not m.exists():
        m=representation.generate(m.parent,plan)
    path=BASE/"replay"
    if not (path/"VERIFY.json").exists():
        launch(path,m,plan["options"],REPEATS,2270481,710,timeout=CAP,
               extra_flags_by_arm={arm:["--arrays-exp"] for arm in plan["options"]})
    publish(analyse(path,m,fresh))


if __name__=="__main__":
    main("--reuse" not in sys.argv)
