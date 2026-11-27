"""Repeat fixed portable library traces while varying the session budget."""
from collections import Counter
import os

import numpy as np

from portfolio import MENU, frac, group_draws
from replay import ROOT, digest, ev, launch, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci, replay_cube

BASE = ROOT / "runs/22-library-caps"
SCOPE = ROOT / "protocols/library-caps.json"
INPUTS = {
    "Четыре проекта": ROOT / "runs/12-new-libraries/portable-corpus/manifest.json",
    "Пять других проектов": ROOT / "runs/17-arrays-options/portable/manifest.json",
}
CAPS = (.75, 1.5, 3.0)
REPEATS = 8


def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    BASE.mkdir(exist_ok=True)
    sessions = []
    seen = {}
    duplicates = []
    for name, manifest in INPUTS.items():
        for item in ev.verify(manifest):
            previous = seen.get(item["sha256"])
            if previous is not None:
                duplicates.append({"kept": previous, "removed": item["group"], "sha256": item["sha256"]})
                continue
            file = os.path.relpath(manifest.parent / item["file"], BASE)
            sessions.append({**item, "file": file, "cohort": name})
            seen[item["sha256"]] = item["group"]
    assert len({p["group"] for p in sessions}) == len(sessions)
    assert len({p["sha256"] for p in sessions}) == len(sessions)
    manifest = BASE / "manifest.json"
    write_json(manifest, {"sessions": sessions, "group_count": len(sessions),
                         "scope": "reused portable library traces; diagnostic cap transfer"})
    ev.verify(manifest)
    menu = load(MENU)
    scope = {
        "locked": now(), "caps_seconds": CAPS, "repeats": REPEATS,
        "source_manifests": {str(p.relative_to(ROOT)): digest(p) for p in INPUTS.values()},
        "cross_cohort_exact_duplicates": duplicates,
        "manifest_sha256": digest(manifest), "configuration_c05": menu["arms"][menu["global_arm"]],
        "common_flags": ["--arrays-exp"], "alias_cap": 1.5,
        "condition": "All distinct fixed streams from two separate source cohorts, paired across caps, randomized within method and repeat. No new holdout claim.",
        "primary": "C05/default PAR2 for each cap, paired across methods and resampled by project; 3.0 minus 0.75 change in log ratio.",
        "secondary": "Conditional solved difference, default rescues/losses, short-cap censoring of independently replayed long runs and project sensitivity.",
    }
    write_json(SCOPE, scope)
    SCOPE.chmod(0o444)
    return scope


def analyse(directory, manifest):
    _, groups, arms, scores, status, wall = replay_cube(directory)
    items = {item["group"]: item for item in load(manifest)["sessions"]}
    keys = {"Все проекты": np.arange(len(groups))}
    keys.update({name: np.array([i for i, g in enumerate(groups) if items[g]["cohort"] == name])
                 for name in INPUTS})
    verification = load(directory / "VERIFY.json")
    outcomes = {}
    for label, take in keys.items():
        projects = [items[groups[i]]["family"] for i in take]
        draw = group_draws(projects, np.random.default_rng(514183))
        means = scores[take].mean(axis=2)
        by_cap, bootstrap_ratios = {}, {}
        for cap in CAPS:
            default, tuned = [arms.index(f"{arm}@{cap}") for arm in ("default", "C05")]
            sd, st = status[take, default], status[take, tuned]
            both = (sd == "solved") & (st == "solved")
            diffs = (wall[take, tuned] - wall[take, default])[both]
            ds, ts = means[:, default], means[:, tuned]
            bootstrap_ratios[cap] = (draw @ ts) / (draw @ ds)
            stats = {
                "par2_default": float(ds.mean()), "par2_c05": float(ts.mean()),
                "ratio": frac(ts, ds, draw),
                "default_status": Counter(sd.ravel()), "c05_status": Counter(st.ravel()),
                "rescues": int(np.sum((sd == "timeout") & (st == "solved"))),
                "losses": int(np.sum((sd == "solved") & (st == "timeout"))),
                "complete_pairs": int(both.sum()),
                "median_delta_ms": float(np.median(diffs) * 1000) if diffs.size else None,
                "leave_one_project_out": {},
            }
            for project in sorted(set(projects)):
                remaining = np.array([k != project for k in projects])
                stats["leave_one_project_out"][project] = float(ts[remaining].mean()/ds[remaining].mean())
            by_cap[str(cap)] = stats
        short, long = CAPS[0], CAPS[-1]
        contrast = np.log(bootstrap_ratios[long]) - np.log(bootstrap_ratios[short])
        recap = {}
        for arm in ("default", "C05"):
            ilow, ihigh = (arms.index(f"{arm}@{cap}") for cap in (short, long))
            low_solved = status[take, ilow] == "solved"
            long_solved = status[take, ihigh] == "solved"
            emulated = long_solved & (wall[take, ihigh] <= short)
            recap[arm] = {
                "solved_short": int(low_solved.sum()), "solved_long": int(long_solved.sum()),
                "solved_long_then_censored": int(emulated.sum()),
                "classification_flips": int((low_solved != emulated).sum()),
                "observed_short_par2": float(means[:, ilow].mean()),
                "recensored_long_par2": float(np.where(emulated, wall[take, ihigh], 2*short).mean()),
            }
        control, alias = (arms.index(f"{arm}@1.5") for arm in ("default", "default_alias"))
        both = (status[take, control] == "solved") & (status[take, alias] == "solved")
        diffs = np.abs((wall[take, control] - wall[take, alias])[both])
        outcomes[label] = {
            "programs": len(take), "projects": len(set(projects)), "by_cap": by_cap,
            "change_log_ratio": [
                float(np.log(by_cap[str(long)]["ratio"][0]/by_cap[str(short)]["ratio"][0])),
                np.percentile(contrast, [2.5, 97.5]).tolist(),
            ],
            "alias_complete_pairs": int(diffs.size),
            "alias_median_ms": float(1000*np.median(diffs)) if diffs.size else None,
            "recensor": recap,
        }
    result = {"programs": len(groups), "repeats": REPEATS, "rows": verification["rows"],
              "status": verification["counts"], "cohorts": outcomes, "bootstrap_seed": 514183}
    write_json(directory / "metrics.json", result)
    return result


def publish(result):
    n, r = result["programs"], result["repeats"]
    all_data = result["cohorts"]["Все проекты"]
    primary = all_data["by_cap"]
    summary = (
        f"На {n} библиотечных методах C05/default равен {fmt_ci(primary['0.75']['ratio'])} "
        f"при 0,75 с, {fmt_ci(primary['1.5']['ratio'])} при 1,50 с и "
        f"{fmt_ci(primary['3.0']['ratio'])} при 3,00 с."
    )
    rows, recap = [], []
    for label, cohort in result["cohorts"].items():
        for cap, stats in cohort["by_cap"].items():
            invalid = sum(stats[f"{arm}_status"].get(k, 0)
                          for arm in ("default", "c05") for k in ("incorrect", "unknown", "error"))
            rows.append([label, cohort["programs"]*r, cap, f"{stats['par2_default']:.3f}",
                         f"{stats['par2_c05']:.3f}", fmt_ci(stats["ratio"]), stats["rescues"],
                         stats["losses"], stats["complete_pairs"], f"{stats['median_delta_ms']:+.1f}",
                         invalid])
        for arm, stats in cohort["recensor"].items():
            recap.append([label, arm, stats["solved_short"], stats["solved_long"],
                          stats["solved_long_then_censored"], stats["classification_flips"]])
    b, (lo, hi) = all_data["change_log_ratio"]
    removed = len(load(SCOPE)["cross_cohort_exact_duplicates"])
    write_json(ROOT / "evidence/23-library-caps.json", {
        "title": "23 · C05 на библиотечных сеансах при разных лимитах",
        "done": f"Проверены {result['rows']} парных измерения C05 и default при трёх лимитах.",
        "summary": summary,
        "paragraphs": [
            f"Повторно использованы {n} различных переносимых записей из двух наборов, {all_data['projects']} проектов. Удалено {removed} точных межнаборных дубликатов. {r} повторов, {result['rows']} отдельных процессов cvc5 1.4.1 с общим --arrays-exp. Флаги и пределы перемешаны в каждом блоке метода и повтора.",
            f"Разность логарифмов отношения PAR2 при 3,00 и 0,75 с составила {b:+.3f} [{lo:+.3f}; {hi:+.3f}]. Интервалы пересэмплируют целые проекты. При 1,50 с медиана абсолютной разницы с дублем default {all_data['alias_median_ms']:.1f} мс на {all_data['alias_complete_pairs']} завершённых парах.",
            "Предел увеличивает и количество завершившихся сеансов, и цену каждого оставшегося таймаута. В таблице знак медианы относится только к парам с двумя корректными завершениями. В отдельном пересчёте длинные повторы снова порогованы по 0,75 с, чтобы оценить нестабильность исхода около короткого предела.",
        ],
        "tables": [
            card_table("PAR2 и переходы через таймаут · число сеансов на режим в каждой строке",
                       ["Набор", "Сеансы", "Лимит, с", "Default PAR2, с", "C05 PAR2, с",
                        "C05/default [95% ДИ]", "Таймаут default решён", "Новый таймаут", "Полные пары",
                        "Медиана Δ, мс", "Прочие сбои обоих режимов"], rows),
            card_table("Фактический предел 0,75 с и пересчёт независимо измеренного прогона при 3,00 с",
                       ["Набор", "Режим", "Решено 0,75 с", "Решено 3,00 с", "Длинный прогон ≤0,75 с", "Расхождения с коротким"], recap),
        ],
        "paths": ["protocols/library-caps.json", "runs/22-library-caps/manifest.json",
                  "runs/22-library-caps/replay/plan.json", "runs/22-library-caps/replay/worker-{0..5}.jsonl",
                  "runs/22-library-caps/replay/VERIFY.json", "runs/22-library-caps/replay/metrics.json"],
        "recommendation": "При переносе настройки выбирать лимит по задаче клиента и отдельно смотреть парные переходы через таймаут.",
        "next": "На целевом живом приложении зафиксировать один рабочий предел и проверить сквозную долю завершённых методов.",
    })
    build()


def main():
    scope = freeze()
    manifest = BASE / "manifest.json"
    assert digest(manifest) == scope["manifest_sha256"]
    configs = {f"{name}@{cap}": params for cap in CAPS
               for name, params in (("default", {}), ("C05", scope["configuration_c05"]))}
    configs["default_alias@1.5"] = {}
    directory = BASE / "replay"
    if not (directory / "VERIFY.json").exists():
        launch(directory, manifest, configs, REPEATS, 793105, 1130,
               timeout_by_arm={arm: float(arm.split("@")[1]) for arm in configs},
               extra_flags_by_arm={arm: scope["common_flags"] for arm in configs})
    publish(analyse(directory, manifest))


if __name__ == "__main__":
    main()
