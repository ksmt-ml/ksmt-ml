"""Confirm the library rewriting result on projects unexposed to the campaign."""
from collections import Counter
import os
from pathlib import Path

import numpy as np

from arrays_options import BASE as DISCOVERY, SCOPE as DISCOVERY_SCOPE
from bridge import independent_single
from portfolio import frac, group_draws
from replay import ROOT, digest, ev, launch, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci, replay_cube
from transfer import capture

BASE = ROOT / "runs/23-rewrite-transfer"
SCOPE = ROOT / "protocols/rewrite-transfer.json"
REPEATS = 10
CAP = 1.5
MAVEN = Path.home() / ".m2/repository"
ARTIFACTS = (
    ("commons-math3", "org/apache/commons", "3.6.1", "org.apache.commons.math3.util.", 20),
    ("commons-collections4", "org/apache/commons", "4.4", "org.apache.commons.collections4.", 22),
    ("jackson-core", "com/fasterxml/jackson/core", "2.17.2", "com.fasterxml.jackson.core.", 22),
    ("antlr4-runtime", "org/antlr", "4.13.2", "org.antlr.v4.runtime.", 20),
    ("plexus-utils", "org/codehaus/plexus", "3.5.1", "org.codehaus.plexus.util.", 22),
    ("commons-text", "org/apache/commons", "1.12.0", "org.apache.commons.text.", 18),
)
PRIOR = (
    ROOT / "runs/04-transfer/corpus-recovered/manifest.json",
    ROOT / "runs/11b-array-bridge/tuple/paired-manifest.json",
    ROOT / "runs/12-new-libraries/portable-corpus/manifest.json",
    DISCOVERY / "portable/manifest.json",
)


def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    earlier = load(DISCOVERY_SCOPE)
    evidence = DISCOVERY / "replay/metrics.json"
    outcome = load(evidence)
    rew = outcome["states"]["global_rewrite"]["ratio"]
    assert rew[0] < 0.95 and rew[1][1] < 1
    input_projects = {item["family"] for path in PRIOR for item in load(path)["sessions"]}
    assert not input_projects.intersection(a[0] for a in ARTIFACTS)
    artifacts = []
    for project, group, version, package, maximum in ARTIFACTS:
        jar = MAVEN / group / project / version / f"{project}-{version}.jar"
        assert jar.is_file(), jar.name
        artifacts.append({
            "project": project, "jar": str(jar), "sha256": digest(jar), "package": package,
            "max_classes": maximum, "method_ms": 7500, "solver_ms": 2200,
        })
    configs = {"default": {}, "default_alias": {}, "C05": earlier["configs"]["global"],
               "rewrite": earlier["configs"]["global_rewrite"]}
    smoke = DISCOVERY / "smoke/VERIFY.json"
    tiny = load(smoke)
    corresponding = {"default": "default", "default_alias": "default_alias",
                     "C05": "global", "rewrite": "global_rewrite"}
    assert tiny["ok"]
    for new_arm, tested_arm in corresponding.items():
        outcomes = [case["status"] for case in tiny["rows"] if case["arm"] == tested_arm]
        assert len(outcomes) == 2 and not set(outcomes) - {"solved", "timeout"}
        assert configs[new_arm] == earlier["configs"][tested_arm]
        assert earlier["extra"][tested_arm] == ["--arrays-exp"]
    result = {
        "locked": now(), "hypothesis": "Can the previously stronger preprocessing subset of C05 beat default on separate library project families, and how does it compare with full C05?",
        "development_sha256": digest(evidence), "development_rewrite_ratio": rew,
        "same_vector_black_box_smoke_sha256": digest(smoke),
        "artifacts": artifacts, "prior_manifests": {str(p.relative_to(ROOT)): digest(p) for p in PRIOR},
        "minimum_admitted_total": 18, "minimum_admitted_projects": 3,
        "array_bridge": earlier["array_bridge"], "configs": configs, "common_flags": ["--arrays-exp"],
        "cap_s": CAP, "repeats": REPEATS,
        "admission": "One method per driver-selected public concrete class. Use whole Z3-audited traces, drop exact hash overlaps with prior library phases and internal duplicates before cvc5 replay. Report every solver failure, independently of options.",
        "primary": "Rewrite/default ratio of paired PAR2, clustered by entire project. Secondary predeclared rewrite/C05 and C05/default, leave-one-project-out and paired timeout changes.",
        "model_access": "None; fixed options carried into local traces after locking.",
    }
    write_json(SCOPE, result)
    SCOPE.chmod(0o444)
    return result


def independent_manifest(source, plan):
    destination = BASE / "independent-manifest.json"
    if destination.exists():
        return destination
    old = {}
    for key, fingerprint in plan["prior_manifests"].items():
        path = ROOT / key
        assert digest(path) == fingerprint
        for item in load(path)["sessions"]:
            old[item["sha256"]] = item["group"]
    portable = load(source)
    rejected = []
    accepted = []
    for row in portable["sessions"]:
        exact = old.get(row["sha256"])
        if exact is not None:
            rejected.append({"group": row["group"], "same_as": exact, "sha256": row["sha256"]})
            continue
        recorded = os.path.relpath(source.parent / row["file"], BASE)
        trace = os.path.relpath(source.parent / row["trace"], BASE)
        assert digest(BASE / trace) == row["trace_sha256"]
        accepted.append({**row, "file": recorded, "trace": trace})
        old[row["sha256"]] = row["group"]
    write_json(destination, {
        **portable, "group_count": len(accepted), "sessions": accepted,
        "prior_exact_duplicates": rejected, "portable_manifest_sha256": digest(source),
    })
    ev.verify(destination)
    destination.chmod(0o444)
    return destination


def summarize(directory, manifest, plan):
    _, groups, arms, scores, status, wall = replay_cube(directory)
    data = load(manifest)
    info = {row["group"]: row for row in data["sessions"]}
    projects = [info[group]["family"] for group in groups]
    draws = group_draws(projects, np.random.default_rng(558253))
    v = {arm: scores[:, i].mean(axis=1) for i, arm in enumerate(arms)}
    base = arms.index("default")
    effects = {}
    for i, arm in enumerate(arms):
        pairs = (status[:, base] == "solved") & (status[:, i] == "solved")
        effects[arm] = {
            "par2_s": float(v[arm].mean()), "to_default": frac(v[arm], v["default"], draws),
            "to_c05": frac(v[arm], v["C05"], draws), "counts": Counter(status[:, i].ravel()),
            "rescued": int(np.sum((status[:, base] == "timeout") & (status[:, i] == "solved"))),
            "new_timeouts": int(np.sum((status[:, base] == "solved") & (status[:, i] == "timeout"))),
            "both_solved": int(pairs.sum()),
            "conditional_median_ms": float(np.median((wall[:, i]-wall[:, base])[pairs])*1000) if pairs.any() else None,
            "leave_project_out": {},
        }
        for project in sorted(set(projects)):
            keep = np.array([p != project for p in projects])
            effects[arm]["leave_project_out"][project] = float(v[arm][keep].mean()/v["default"][keep].mean())
    project_rows = {}
    for project in sorted(set(projects)):
        take = np.flatnonzero(np.asarray(projects) == project)
        project_rows[project] = {"programs": len(take),
                                 "ratios": {a: float(v[a][take].mean()/v["default"][take].mean()) for a in arms}}
    control = arms.index("default_alias")
    both = (status[:, base] == "solved") & (status[:, control] == "solved")
    alias = np.abs((wall[:, base] - wall[:, control])[both])
    result = {
        "programs": len(groups), "projects": project_rows, "repeats": REPEATS,
        "rows": load(directory / "VERIFY.json")["rows"], "effects": effects,
        "duplicate_count": len(data["prior_exact_duplicates"]), "adapter_excluded": data["adapter_excluded"],
        "source_counts": data["counts"], "failed_projects": data["failed_projects"],
        "alias_solved_pairs": int(alias.size), "alias_median_abs_ms": float(np.median(alias)*1000),
        "scope_sha256": digest(SCOPE), "settings": plan["configs"],
    }
    write_json(directory / "metrics.json", result)
    return result


def publish(result):
    n, r = result["programs"], result["repeats"]
    e = result["effects"]
    p = len(result["projects"])
    summary = (
        f"На {n} новых методах из {p} проектов подмножество переписывания дало "
        f"{fmt_ci(e['rewrite']['to_default'])} к default и {fmt_ci(e['rewrite']['to_c05'])} к C05."
    )
    write_json(ROOT / "evidence/24-rewrite-transfer.json", {
        "title": "24 · Закрытая проверка подмножества переписывания",
        "done": f"Проверен перенос флагов переписывания на {n} новых методах из {p} проектов.",
        "summary": summary,
        "paragraphs": [
            f"До записи зафиксированы шесть кэшированных JAR из ранее не использованных проектных семейств и точные флаги с пройденной пробой. После аудита Z3, преобразования массивов и контроля точных дубликатов принято {n} полных сеансов, по одному методу на класс. Удалено {result['duplicate_count']} точных совпадений и {len(result['adapter_excluded'])} непреобразованных записей. Фактически допущено {p} проектов.",
            f"Общий --arrays-exp. По {r} случайно упорядоченных парных повторов, {result['rows']} запусков cvc5 1.4.1. Основной контраст переписывание к default. Интервал пересэмплирует целые проекты. В контроле default/дубль медиана абсолютной разницы {result['alias_median_abs_ms']:.1f} мс на {result['alias_solved_pairs']} парах полных сеансов.",
            f"Векторы переписывания и C05 взяты из предшествующей серии, без повторной их оптимизации. PAR2 включает удвоенный предел для любого незавершённого или некорректного сеанса. C05 к default {fmt_ci(e['C05']['to_default'])}.",
            f"Ошибки записаны по каждому режиму. Проектов с отказом захвата {len(result['failed_projects'])}. Число проверенных проектов ограничивает переносимость интервала.",
        ],
        "tables": [
            card_table(f"Закрытая проверка · по {n*r} сеансов на режим",
                       ["Режим", "PAR2, с", "К default [95% ДИ]", "К C05 [95% ДИ]", "Таймауты",
                        "Таймаут default решён", "Новый таймаут", "Ошибки", "Unknown", "Несовпадения"],
                       [[arm, f"{item['par2_s']:.3f}", fmt_ci(item["to_default"]), fmt_ci(item["to_c05"]),
                         item["counts"].get("timeout", 0), item["rescued"], item["new_timeouts"],
                         *[item["counts"].get(k, 0) for k in ("error", "unknown", "incorrect")]]
                        for arm, item in e.items()]),
            card_table("Число методов и отношение PAR2 на каждом проекте",
                       ["Проект", "Методов", "Переписывание/default", "C05/default"],
                       [[name, item["programs"], f"{item['ratios']['rewrite']:.3f}", f"{item['ratios']['C05']:.3f}"]
                        for name, item in result["projects"].items()]),
        ],
        "paths": ["protocols/rewrite-transfer.json", "runs/23-rewrite-transfer/corpus/",
                  "runs/23-rewrite-transfer/portable/manifest.json", "runs/23-rewrite-transfer/independent-manifest.json",
                  "runs/23-rewrite-transfer/replay/VERIFY.json", "runs/23-rewrite-transfer/replay/metrics.json"],
        "recommendation": "Сохранить общий default. При подтверждённом выигрыше сравнить переписывание и C05 на целевом живом клиенте с тем же полным потоком операций.",
        "next": "Проверить переписывание при рабочем дедлайне на целевых методах с тем же классом CPU.",
    })
    build()


def main():
    plan = freeze()
    raw = BASE / "corpus/manifest.json"
    if not raw.exists():
        raw = capture(raw.parent, plan, SCOPE)
    portable = BASE / "portable/manifest.json"
    if not portable.exists():
        portable = independent_single(raw, portable.parent, plan["array_bridge"])
    manifest = independent_manifest(portable, plan)
    admitted = load(manifest)["sessions"]
    assert len(admitted) >= plan["minimum_admitted_total"]
    assert len({row["family"] for row in admitted}) >= plan["minimum_admitted_projects"]
    test = BASE / "replay"
    if not (test / "VERIFY.json").exists():
        launch(test, manifest, plan["configs"], REPEATS, 834207, 1300, timeout=CAP,
               extra_flags_by_arm={arm: plan["common_flags"] for arm in plan["configs"]})
    publish(summarize(test, manifest, plan))


if __name__ == "__main__":
    main()
