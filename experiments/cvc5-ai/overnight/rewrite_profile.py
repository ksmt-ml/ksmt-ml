"""Attribute the library rewriting effect using paired whole-session markers."""
from collections import Counter, defaultdict
import os
import sys
import time

import numpy as np

from portfolio import frac, group_draws
import profile_probe as profile
from replay import CORES, PYTHON, ROOT, STOP, digest, lines, load, now
from report import build, write_json
from rewrite_transfer import BASE as LIBRARY, SCOPE as REWRITE
from search import execute_workers
from stability_metrics import card_table, fmt_ci

BASE = ROOT / "runs/27-rewrite-profile"
SCOPE = ROOT / "protocols/rewrite-profile.json"
REPEATS = 6
CAP = 1.5
LIMIT_PER_PROJECT = 5


def install_conditions():
    profile.BASE = BASE
    profile.SCOPE = SCOPE
    profile.PORTABLE = True
    profile.REPEATS = REPEATS
    profile.CAP = CAP


def prepare():
    if SCOPE.exists():
        return load(SCOPE)
    BASE.mkdir(exist_ok=True)
    scope = load(REWRITE)
    manifest = LIBRARY / "independent-manifest.json"
    by_project = defaultdict(list)
    for row in load(manifest)["sessions"]:
        if row["operations"].get("model", 0) > 0 and len(row["oracle"]) >= 4:
            by_project[row["family"]].append(row)
    selected = [{**r, "origin": "повторно использованная библиотека",
                 "frozen_file": os.path.relpath(manifest.parent/r["file"], ROOT)}
                for key, rows in sorted(by_project.items())
                for r in sorted(rows, key=lambda x: x["sha256"])[:LIMIT_PER_PROJECT]]
    assert len(selected) >= 12 and len({r["family"] for r in selected}) >= 3
    recording = {
        "locked": now(), "question": "Does the reproducible library effect appear in check intervals or model/core output?",
        "source_manifest_sha256": digest(manifest), "turns": REPEATS, "cap_s": CAP,
        "options": {k: scope["configs"][k] for k in ("default", "rewrite", "C05")},
        "cohort": [{"program": r["group"], "family": r["family"], "sha256": r["sha256"]} for r in selected],
        "primary": "Rewrite minus default event components, only if both ordinary and both profiled cases finish in the same block; compute paired bootstrap by project.",
        "secondary": "C05 analogously, ordinary-only PAR2 and profiled-to-ordinary overhead per arm, with invalid runs explicit.",
        "exploratory": "Mechanism follow-up on already released library recordings. No new independent performance estimate.",
    }
    profile.prepare_inputs(selected)
    write_json(SCOPE, recording)
    SCOPE.chmod(0o444)
    return recording


def components(row, entry):
    durations = Counter()
    for (i, _, left), (j, _, right) in zip(row["markers"][::2], row["markers"][1::2], strict=True):
        assert i == j
        durations[entry["tags"][i]] += right-left
        durations["coalesced"] += int(right == left)
    total = sum(durations[k] for k in ("check", "model", "core"))
    return [durations[k] for k in ("check", "model", "core")] + [max(0, row["wall_s"]-total), row["wall_s"], durations["coalesced"]]


def summarize(scope, entries, records):
    measured=len(records)
    records=[row for row in records if row["repeat"]<REPEATS]
    assert len(records)==len(entries)*REPEATS*len(scope["options"])*2
    by = {(r["group"], r["repeat"], r["arm"], r["mode"]): r for r in records}
    outputs = {}
    labels = ("check", "model", "core", "other", "wall")
    for arm in ("rewrite", "C05"):
        scores = defaultdict(list)
        completed = defaultdict(list)
        coalesced = [0, 0]
        intervals = 0
        for entry in entries:
            name = entry["group"]
            for rep in range(REPEATS):
                a, b, c, d = (by[name, rep, cfg, mode] for cfg in ("default", arm)
                              for mode in ("ordinary", "profiled"))
                scores[name].append([a["par2"], c["par2"]])
                if any(x["status"] != "solved" for x in (a, b, c, d)):
                    continue
                q, r = (components(row, entry) for row in (b, d))
                completed[name].append([a["wall_s"], c["wall_s"], *q[:5], *r[:5]])
                coalesced[0] += q[-1]
                coalesced[1] += r[-1]
                intervals += len(entry["tags"])
        families = {v["group"]: v["family"] for v in entries}
        keep = sorted(completed)
        assert keep
        all_cases = sorted(scores)
        full = np.array([np.mean(scores[g], axis=0) for g in all_cases])
        draw = group_draws([families[g] for g in all_cases], np.random.default_rng(391731))
        paired = np.array([np.mean(completed[g], axis=0) for g in keep])
        filtered = group_draws([families[g] for g in keep], np.random.default_rng(86188))
        contrast = paired[:, 7:12] - paired[:, 2:7]
        resampled = (filtered @ contrast)/filtered.sum(axis=1)[:, None]
        ci = np.percentile(resampled, [2.5, 97.5], axis=0)
        outputs[arm] = {
            "ordinary_par2_ratio": frac(full[:, 1], full[:, 0], draw),
            "joint_complete_pairs": sum(map(len, completed.values())), "joint_complete_methods": len(keep),
            "ordinary_elapsed_ratio": frac(paired[:, 1], paired[:, 0], filtered),
            "echo_vs_ordinary_default": frac(paired[:, 6], paired[:, 0], filtered),
            "echo_vs_ordinary_tuned": frac(paired[:, 11], paired[:, 1], filtered),
            "timing_deltas_ms": {key: [float(1000*contrast[:, i].mean()), (1000*ci[:, i]).tolist()]
                                 for i, key in enumerate(labels)},
            "coalesced_both_modes": coalesced, "count_per_mode": intervals,
        }
    statuses = {mode: {a: Counter(r["status"] for r in records if r["mode"] == mode and r["arm"] == a)
                       for a in scope["options"]} for mode in ("ordinary", "profiled")}
    results = {"programs": len(entries), "projects": len({r["family"] for r in entries}),
               "repeats": REPEATS, "rows": len(records), "physical_rows": measured,
               "extra_rows": measured-len(records),"modes": outputs, "counts": statuses}
    write_json(BASE/"metrics.json", results)
    return results


def publish(result):
    n, effects = result["programs"], result["modes"]
    rewrite = effects["rewrite"]
    check, model = (rewrite["timing_deltas_ms"][k][0] for k in ("check", "model"))
    summary = f"В {rewrite['joint_complete_pairs']} полных парах переписывание изменило время check на {check:+.1f} мс, get-model на {model:+.1f} мс."
    rows = []
    for mode, data in effects.items():
        for label, (estimate, (lo, hi)) in data["timing_deltas_ms"].items():
            rows.append([mode, label, data["joint_complete_methods"], data["joint_complete_pairs"],
                         f"{estimate:+.1f} [{lo:+.1f}; {hi:+.1f}]"])
    note=(f"Первый запуск по ошибке унаследовал два дополнительных повтора профилировщика. "
          f"Проверены все {result['physical_rows']} записей. Основной расчёт содержит зафиксированные {result['rows']} сеансов. "
          f"Остальные {result['extra_rows']} сохранены в журнале под номерами дополнительных повторов."
          if result["extra_rows"] else "Все измерения в точности соответствовали шести заранее указанным повторам.")
    write_json(ROOT/"evidence/27-rewrite-profile.json", {
        "title": "27 · Вклад операций в эффект переписывания",
        "done": f"Сопоставлены {result['rows']} повторов с маркерами операции и без них.",
        "summary": summary,
        "paragraphs": [
            f"Повторно использованы {n} методов из {result['projects']} проектов. {REPEATS} повторов, по два режима маркеров на вариант флагов. Порядок случайный, предел 1,5 с, общий --arrays-exp.",
            "Сравнение длительностей по одним и тем же парам требует корректного завершения всех четырёх сеансов: обе настройки, обычная и инструментированная. Интервалы наблюдают поток вывода и могут совпадать при чтении одним блоком. Это измерение команд cvc5 CLI.",
            f"Для переписывания профилированное к обычному время равно {fmt_ci(rewrite['echo_vs_ordinary_tuned'])}, общий default {fmt_ci(rewrite['echo_vs_ordinary_default'])}. Отношение PAR2 по всем обычным сеансам {fmt_ci(rewrite['ordinary_par2_ratio'])}.",
            note,
        ],
        "tables": [
            card_table("Профилированный режим минус общий default · проектный 95% ДИ",
                       ["Режим", "Команды", "Методов в сравнении", "Полных пар", "Разность, мс"], rows),
            card_table(f"Полные исходы · по {n*REPEATS} запусков на строку",
                       ["Инструментация", "Режим", "Решено", "Таймаут", "Ошибка", "Unknown", "Несовпадение"],
                       [[mode, a, *[data.get(k, 0) for k in ("solved", "timeout", "error", "unknown", "incorrect")]]
                        for mode, cases in result["counts"].items() for a, data in cases.items()]),
        ],
        "paths": ["protocols/rewrite-profile.json", "runs/27-rewrite-profile/inputs.json",
                  "runs/27-rewrite-profile/worker-{0..5}.jsonl", "runs/27-rewrite-profile/VERIFY.json",
                  "runs/27-rewrite-profile/metrics.json"],
        "recommendation": "При измерении эффекта переписывания отдельно контролировать смену исхода таймаута и длительность check.",
        "next": "Проверить наблюдаемую фазу на тех же операциях живого клиента с поддерживающей их версией.",
    })
    build()


def main():
    install_conditions()
    scope = prepare()
    entries = load(BASE/"inputs.json")
    profile.gate(entries, scope)
    if (BASE/"VERIFY.json").exists():
        rebuild()
        return
    jobs = [([PYTHON, "-B", __file__, "worker", str(i)], BASE/f"worker-{i}.out") for i in range(len(CORES))]
    assert not any(execute_workers(jobs, min(1200, STOP-time.time()-12)))
    publish(summarize(scope, entries, profile.verify(scope, entries)))


def rebuild():
    install_conditions()
    scope=load(SCOPE)
    entries=load(BASE/"inputs.json")
    profile.REPEATS=max(row["repeat"] for p in BASE.glob("worker-*.jsonl") for row in lines(p))+1
    publish(summarize(scope,entries,profile.verify(scope,entries)))


if __name__ == "__main__":
    if len(sys.argv) == 3:
        install_conditions()
        profile.worker(int(sys.argv[2]))
    elif len(sys.argv)==2 and sys.argv[1]=="rebuild":
        rebuild()
    else:
        main()
