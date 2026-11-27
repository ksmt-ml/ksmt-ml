"""Measure the exact two-to-six worker gap that separated old warm and late search."""
from collections import Counter
import itertools
import random

import numpy as np

from concurrency_probe import measure_block
from replay import CORES, DATA, ROOT, check_budget, digest, lines, load, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE = ROOT / "runs/28-warm-load"
SCOPE = ROOT / "protocols/warm-load.json"
TRAIN = DATA / "train/manifest.json"
CALIBRATION = ROOT / "runs/14b-search-retiming/metrics.json"
SLATE = ROOT / "protocols/search-retiming-slates.json"
SEED = 18713
BLOCKS = 10
MODES = {"two": (1,), "six": (1, 2, 3, 4, 5)}


def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    retime = load(CALIBRATION)["seed_analysis"][str(SEED)]
    pick = retime["first_half_chooses_second_half_scores"]
    selection = {label: pick[label]["first"] for label in ("warm", "late")}
    slate = load(SLATE)["seeds"][str(SEED)]["configs"]
    cfg = {"default": {}, **{label: slate[key] for label, key in selection.items()}}
    assert len({str(v) for v in cfg.values()}) == 3
    rng = random.Random(283655)
    ordering = [{"id": i, "order": rng.sample(list(MODES), 2), "paired_order_seed": rng.randrange(2**31)}
                for i in range(BLOCKS)]
    scope = {
        "locked": now(), "question": "Does two versus six solvers change the relative ordering of a fixed warm and a fixed late candidate?",
        "configs": cfg, "train_sha256": digest(TRAIN), "chosen_seed": SEED, "chosen_train_only": selection,
        "source_retiming_sha256": digest(CALIBRATION), "source_slate_sha256": digest(SLATE),
        "target_core": CORES[0], "measure_core": CORES[0], "background_affinity": {k:[CORES[i] for i in v] for k,v in MODES.items()},
        "time_pairs": ordering, "full_session_cap_s": 1.5,
        "primary": "Six/two default PAR2. Ratio of late/warm PAR2 across two modes, with crossed bootstrap of programs and temporal paired blocks. Exact randomized paired sign test for default raw absolute shift.",
        "cohort_scope": "All 15 original tuning programs; diagnostic only. First original search seed fixed by ID. Warm and late candidates set from training retiming only.",
    }
    write_json(SCOPE, scope)
    SCOPE.chmod(0o444)
    return scope


def analyze(scope):
    block_pairs = scope["time_pairs"]
    every = {}
    conditional = {}
    counts = {mode: {arm: Counter() for arm in scope["configs"]} for mode in MODES}
    for pair in block_pairs:
        for mode in MODES:
            folder = BASE/f"pair-{pair['id']:02d}"/mode
            plan, names, arms, scores, status, wall = replay_cube(folder)
            assert plan["cores"] == [scope["measure_core"]]
            assert plan["manifest_sha256"] == scope["train_sha256"]
            assert plan["seed"] == pair["paired_order_seed"]
            assert len(names)==15 and list(arms)==list(scope["configs"])
            by = {(row["group"], row["arm"]):row["solver_cpu_s"] for row in lines(folder/"worker-0.jsonl")}
            cpu = np.array([[by[g,a] for a in arms] for g in names])
            every[pair["id"],mode] = scores[...,0]
            conditional[pair["id"],mode] = status[...,0], wall[...,0], cpu
            for i, arm in enumerate(arms):
                counts[mode][arm].update(status[:,i,0])
    totals = {mode: np.array([every[p["id"],mode] for p in block_pairs]) for mode in MODES}
    temporal,n,k = totals["two"].shape
    rng = np.random.default_rng(93699)
    programs = rng.integers(n,size=(B,n))
    pairs = rng.integers(temporal,size=(B,temporal))
    totals_boot = {mode: np.mean(arr[pairs[:,:,None],programs[:,None,:],:],axis=(1,2)) for mode,arr in totals.items()}
    ratios = {}
    for i,arm in enumerate(arms):
        boot = totals_boot["six"][:,i]/totals_boot["two"][:,i]
        ratios[arm] = [float(totals["six"][...,i].mean()/totals["two"][...,i].mean()),
                       np.percentile(boot,[2.5,97.5]).tolist()]
    alt, warm = arms.index("late"), arms.index("warm")
    late_warm = {mode:[float(arr[...,alt].mean()/arr[...,warm].mean()),
                   np.percentile(totals_boot[mode][:,alt]/totals_boot[mode][:,warm],[2.5,97.5]).tolist()]
                for mode,arr in totals.items()}
    both_boot = totals_boot["six"][:,alt]/totals_boot["six"][:,warm]/(
        totals_boot["two"][:,alt]/totals_boot["two"][:,warm])
    both_ratio = [late_warm["six"][0]/late_warm["two"][0], np.percentile(both_boot,[2.5,97.5]).tolist()]
    ix = arms.index("default")
    default_wall,default_cpu = [],[]
    for b in block_pairs:
        index = b["id"]
        ts,tw,tc = conditional[index,"two"]
        ss,sw,sc = conditional[index,"six"]
        complete = (ts[:,ix]=="solved")&(ss[:,ix]=="solved")
        default_wall.extend((sw[:,ix]-tw[:,ix])[complete])
        default_cpu.extend((sc[:,ix]-tc[:,ix])[complete])
    delta = (totals["six"][...,ix]-totals["two"][...,ix]).mean(axis=1)
    flip = np.asarray(list(itertools.product((-1,1),repeat=temporal)))
    random_p = float(np.mean(np.abs(flip@delta)>=abs(delta.sum())-1e-14))
    jobs = sum(len(lines(p)) for p in BASE.glob("pair-*/background-*/load-*.jsonl"))
    result = {"programs":n,"time_pairs":temporal,"repeats_per_mode":temporal,"rows":temporal*n*k*2,
              "counts":counts,"background_rows":jobs,"six_to_two_by_arm":ratios,"late_to_warm_by_load":late_warm,
              "late_warm_interaction":both_ratio,"default_wall_increase_median_ms":float(np.median(default_wall)*1000),
              "default_cpu_increase_median_ms":float(np.median(default_cpu)*1000),
              "default_both_solved":len(default_wall),"default_randomization_p":random_p,
              "par2_by_load_and_arm":{mode:{arm:float(x[...,i].mean()) for i,arm in enumerate(arms)} for mode,x in totals.items()},
              "design_sha256":digest(SCOPE)}
    write_json(BASE/"metrics.json", result)
    return result


def publish(result):
    q=result["six_to_two_by_arm"]
    d=result["late_to_warm_by_load"]
    n=result["programs"]
    pairs=result["time_pairs"]
    summary=f"При переходе с двух на шесть воркеров default дал {fmt_ci(q['default'])}. Отношение эффекта позднего кандидата к стартовому изменилось в {fmt_ci(result['late_warm_interaction'])} раза."
    write_json(ROOT/"evidence/28-warm-load.json", {
        "title":"28 · Нагрузка общей разминки и основного поиска",
        "done":f"Проверены {pairs} парных блоков между режимами двух и шести воркеров.",
        "summary":summary,
        "paragraphs":[
            f"Те же {n} обучающих программ, {pairs} повторов каждого режима. Измеряемый процесс закреплён за CPU {CORES[0]}. Один дополнительный процесс одинаков в обеих половинах, ещё четыре работают после случайно назначенного включения высокой нагрузки.",
            f"Векторы зафиксированы по измерениям первого seed {SEED} на обучении. Базовый поздний/ранний эффект при двух воркерах {fmt_ci(d['two'])}, при шести {fmt_ci(d['six'])}. Оценка диагностическая на этих векторах и этой нагрузке.",
            f"У default {result['default_both_solved']} завершённых пар. Медиана разницы wall {result['default_wall_increase_median_ms']:+.1f} мс, CPU {result['default_cpu_increase_median_ms']:+.1f} мс. Точный тест случайного порядка p={result['default_randomization_p']:.4f}. ДИ одновременно пересэмплирует программы и парные временные блоки.",
            f"Сохранены {result['rows']} целевых и {result['background_rows']} фоновых воспроизведений. В таблице все прочие незавершения включают ошибки разбора, unknown и неправильные ответы."
        ],
        "tables":[card_table(f"Два и шесть воркеров · по {n*pairs} сеансов на режим нагрузки и настройку",
                   ["Настройка","Два PAR2, с","Шесть PAR2, с","Шесть/два [95% ДИ]","Таймауты два","Таймауты шесть",
                    "Прочие сбои два","Прочие сбои шесть"],
                   [[a,f"{result['par2_by_load_and_arm']['two'][a]:.3f}",f"{result['par2_by_load_and_arm']['six'][a]:.3f}",
                    fmt_ci(q[a]),result["counts"]["two"][a]["timeout"],result["counts"]["six"][a]["timeout"],
                    *[sum(result["counts"][load][a][k] for k in ("error","unknown","incorrect")) for load in MODES]] for a in q])],
        "paths":["protocols/warm-load.json","runs/28-warm-load/metrics.json",
                 "runs/28-warm-load/pair-<номер>/<режим>/worker-0.jsonl",
                 "runs/28-warm-load/pair-<номер>/background-<режим>/"],
        "recommendation":"Сохранять одинаковую рабочую нагрузку при измерениях разминки, поиска и валидационных контролей.",
        "next":"При повторной настройке измерять короткий контроль на обоих этапах и сообщать возможный сдвиг относительно него.",
    })
    build()


def main():
    scope = freeze()
    BASE.mkdir(exist_ok=True)
    for pair in scope["time_pairs"]:
        check_budget()
        path = BASE/f"pair-{pair['id']:02d}"
        path.mkdir(exist_ok=True)
        for mode in pair["order"]:
            measure_block(scope, pair, path, mode, manifest=TRAIN, load_slots=MODES[mode], wall_limit=75)
    publish(analyze(scope))


if __name__ == "__main__":
    main()
