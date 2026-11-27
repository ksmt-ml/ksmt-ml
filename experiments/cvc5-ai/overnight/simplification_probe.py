"""Separate simplification=none from the other two rewriting settings."""
from collections import Counter
import os
import time

import numpy as np

from arrays_options import SCOPE as OPTIONS
from portfolio import frac, group_draws
from replay import CORES, ROOT, digest, ev, launch, load, now
from report import build, write_json
from rewrite_transfer import BASE as TRANSFER
from stability_metrics import card_table, fmt_ci, replay_cube
from validate_cores import cvc_capture

BASE = ROOT / "runs/29-simplification"
SCOPE = ROOT / "protocols/simplification-ablation.json"
CAP = 1.5
REPEATS = 8
FRESH = TRANSFER / "independent-manifest.json"
ALTERNATIVE = ROOT / "runs/17-arrays-options/portable/manifest.json"


def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    source = FRESH if FRESH.exists() else ALTERNATIVE
    rewrite = load(OPTIONS)["configs"]["global_rewrite"]
    simple = {key: value for key, value in rewrite.items() if key == "simplification"}
    others = {key: value for key, value in rewrite.items() if key != "simplification"}
    assert simple == {"simplification": "none"} and len(others) == 2
    plan = {
        "locked": now(), "manifest": str(source.relative_to(ROOT)), "manifest_sha256": digest(source),
        "options": {"default": {}, "no_simpl": simple, "other_rewriting": others,
                    "all_rewriting": rewrite, "default_alias": {}},
        "common_flags": ["--arrays-exp"], "repeats": REPEATS, "timeout_s": CAP,
        "question": "Is the library preprocessing effect under cvc5 1.4.1 attributable to simplification=none or another option interaction?",
        "scope": "Exploratory mechanism on all admitted already-recorded classes, no new heldout claim.",
        "primary": "Ratio of simplification-only to all rewriting; ratio of the two other settings with default; factorial contrast on paired PAR2. Bootstrap entire projects.",
    }
    write_json(SCOPE, plan)
    SCOPE.chmod(0o444)
    return plan


def smoke(scope, manifest):
    folder = BASE / "smoke"
    gate = folder / "VERIFY.json"
    if gate.exists():
        assert load(gate)["ok"]
        return
    folder.mkdir(parents=True)
    settings = ["simplification", "ext-rew-prep", "repeat-simp"]
    probe = folder / "options.smt2"
    query_options = [f"(get-option :{key})" for key in settings]
    probe.write_text("\n".join(["(set-logic ALL)", *query_options, "(push 1)", "(assert false)",
                                "(check-sat)", "(pop 1)", "(check-sat)", *query_options, "(exit)"])+"\n")
    short = min(load(manifest)["sessions"], key=lambda a:(len(a["oracle"]), a["sha256"]))
    session = manifest.parent / short["file"]
    assert digest(session) == short["sha256"]
    affinity = os.sched_getaffinity(0)
    collected = {}
    try:
        os.sched_setaffinity(0, {CORES[0]})
        for labels, config in scope["options"].items():
            op = cvc_capture(probe, config, True)
            detail = cvc_capture(session, config, True)
            for name, result in (("options", op), ("session", detail)):
                (folder / (labels+"-"+name+".stdout")).write_text(result["stdout"])
                (folder / (labels+"-"+name+".stderr")).write_text(result["stderr"])
            printout = op["stdout"].splitlines()
            expected = detail["status"] == "solved" and ev.ANSWERS.findall(detail["stdout"]) == short["oracle"]
            collected[labels] = {
                "ok": op["status"] == "solved" and len(printout) == 8 and
                printout[3:5] == ["unsat","sat"] and printout[:3] == printout[5:] and expected,
                "values": dict(zip(settings,printout[:3])),
                "tiny_answers": printout[3:5],
                "session_status": detail["status"],
            }
    finally:
        os.sched_setaffinity(0, affinity)
    ok = all(x["ok"] for x in collected.values())
    write_json(gate, {"at": now(), "ok":ok,"short_session":short["group"],"arms":collected})
    assert ok


def result(path, manifest, scope):
    _, groups, arms, arr, statuses, times = replay_cube(path)
    families = {r["group"]:r["family"] for r in load(manifest)["sessions"]}
    mat = group_draws([families[g] for g in groups],np.random.default_rng(72813))
    scores = {arm:arr[:,j,:].mean(axis=1) for j,arm in enumerate(arms)}
    base = scores["default"]
    bridge = scores["all_rewriting"]
    effect = scores["all_rewriting"] - scores["no_simpl"] - scores["other_rewriting"] + base
    interaction = [float(effect.mean()), np.percentile((mat@effect)/mat.sum(axis=1),[2.5,97.5]).tolist()]
    by_settings = {
        a: {"par2":float(values.mean()),"to_default":frac(values,base,mat),"to_full":frac(values,bridge,mat),
            "statuses":Counter(statuses[:,arms.index(a),:].ravel())} for a,values in scores.items()
    }
    a,b=(arms.index(i) for i in ("default","default_alias"))
    both=(statuses[:,a,:]=="solved")&(statuses[:,b,:]=="solved")
    alias=np.abs((times[:,a,:]-times[:,b,:])[both])
    M={"programs":len(groups),"projects":len(set(families.values())),"repeats":REPEATS,"rows":int(np.prod(arr.shape)),
       "interaction":interaction,"effects":by_settings,"alias_solved_pairs":int(alias.size),
       "alias_abs_median_ms":float(np.median(alias)*1000),"scope_sha256":digest(SCOPE)}
    write_json(path/"metrics.json",M)
    return M


def publish(m):
    n,k,b=m["programs"],m["projects"],m["effects"]
    headline=f"Один simplification=none дал {fmt_ci(b['no_simpl']['to_full'])} к трём флагам переписывания и {fmt_ci(b['no_simpl']['to_default'])} к default."
    mo=load(BASE/"smoke/VERIFY.json")["arms"]
    x,(lo,hi)=m["interaction"]
    write_json(ROOT/"evidence/29-simplification.json",{
        "title":"29 · Раздельные параметры переписывания в cvc5 1.4.1",
        "done":f"Сверены отдельные параметры переписывания на {n} методах.",
        "summary":headline,
        "paragraphs":[
            f"Повторное измерение {n} ранее записанных методов из {k} проектов. {REPEATS} повторов, полный сеанс 1,5 с, {m['rows']} запусков. Все используют общий --arrays-exp. Режимы переписывания и дубликат default перемешаны в каждом блоке.",
            f"Разложение 2×2 отделяет simplification=none от совместных ext-rew-prep=off и repeat-simp=false. Аддитивное взаимодействие {1000*x:+.1f} мс PAR2 [{1000*lo:+.1f}; {1000*hi:+.1f}]. ДИ по проектам. У дубля default медиана абсолютной разницы {m['alias_abs_median_ms']:.1f} мс на {m['alias_solved_pairs']} корректных парах.",
            "В пробе значения прочитаны до и после проверки. Одинаковый вывод get-option допускает внутренние реакции на явное назначение значения. Основные оценки используют фактические новые процессы на исходных полных сеансах.",
        ],
        "tables":[
            card_table(f"По {n*REPEATS} библиотечных сеансов на режим",
                ["Опции","PAR2, с","К default [95% ДИ]","К полному переписыванию [95% ДИ]","Таймауты","Ошибки","Unknown","Несовпадения"],
                [[name,f'{q["par2"]:.3f}',fmt_ci(q["to_default"]),fmt_ci(q["to_full"]),
                 *[q["statuses"].get(x,0) for x in ("timeout","error","unknown","incorrect")]] for name,q in b.items()]),
            card_table("Get-option на краткой инкрементальной пробе · один запуск на режим",
                ["Флаги","simplification","ext-rew-prep","repeat-simp"],
                [[name,*[q["values"][key] for key in ("simplification","ext-rew-prep","repeat-simp")]] for name,q in mo.items()]),
        ],
        "paths":["protocols/simplification-ablation.json","runs/29-simplification/smoke/VERIFY.json",
                 "runs/29-simplification/replay/VERIFY.json","runs/29-simplification/replay/metrics.json",
                 "runs/29-simplification/replay/worker-{0..5}.jsonl"],
        "recommendation":"На целевом клиенте сверить достаточность минимального вектора флагов в его фактической версии cvc5.",
        "next":"Сопоставить тот же переключатель на новых полных методах живого клиента с совместимой версией cvc5.",
    })
    build()


def main():
    BASE.mkdir(exist_ok=True)
    scope=freeze()
    src=ROOT/scope["manifest"]
    assert digest(src)==scope["manifest_sha256"]
    smoke(scope, src)
    output=BASE/"replay"
    if not (output/"VERIFY.json").exists():
        launch(output,src,scope["options"],REPEATS,1721439,680,timeout=CAP,
               extra_flags_by_arm={arm:scope["common_flags"] for arm in scope["options"]})
    publish(result(output,src,scope))


if __name__=="__main__":
    main()
