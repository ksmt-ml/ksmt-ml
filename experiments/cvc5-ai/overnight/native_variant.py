"""Compare the rewriting settings on the same actual native USVM path."""
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import hashlib
import os
import random
import sys
import time

import numpy as np

from live_client import one_case, SCOPE as OLD_LIVE
from native_overlay import BASE, compile_overlay
from native_probe import BASE as PROBE, settings
from portfolio import frac, group_draws
from replay import CORES, ROOT, STOP, digest, lines, load, now
from report import build, write_json
from rewrite_transfer import BASE as FRESH, SCOPE as FRESH_SCOPE
from stability_metrics import card_table, fmt_ci

SCOPE = ROOT / "protocols/native-rewrite.json"
OVERLAY = BASE / "compile/classes"
CONFIRM = PROBE / "api-exceptions-fixed/VERIFY.json"
R = 3
SIZE = 3
JVM_CAP = 39
ARMS = ("default", "rewrite")


def report_compatibility():
    result = load(CONFIRM)
    p = result["arms"]
    headline = "Нативный cvc5 1.1.2 в KSMT 0.5.26 принимает подмножество переписывания. Полный C05 содержит отсутствующий bv-abstraction."
    write_json(ROOT / "evidence/18c-native-options.json", {
        "title": "18в · Совместимость флагов в нативном cvc5",
        "summary": headline,
        "done": "Проверены настройки и операции модели и ядра для встроенного cvc5.",
        "paragraphs": [
            "Три коротких прогона через штатную JNI-библиотеку дали sat → unsat → sat с проверкой модели и извлечением ядра между push и pop. Это синтетическая проба интерфейса.",
            "На версии 1.1.2 из трёх параметров переписывания фактически изменился simplification с batch на none. ext-rew-prep=off и repeat-simp=false уже были значениями по умолчанию. Отсутствующий bv-abstraction делает полный вектор C05 неприменимым без изменения.",
            "Первая Java-проба завершилась на проверяемых исключениях push и pop до запуска библиотеки. Сырой журнал сохранён, после исправления все три процесса прошли тест операций.",
        ],
        "tables": [card_table("JNI · по одной короткой программе и одному повтору на режим",
            ["Режим", "Версия", "Точное применение", "Непринятый параметр", "Проверены модель и ядро"],
            [[arm, row["version"], "да" if row["exact_vector_passed"] else "нет",
              ", ".join(row["unsupported"]) or "—", "да" if row["checked_model_core_incremental"] else "нет"]
             for arm, row in p.items()])],
        "paths": ["NativeOptionsProbe.java", "runs/24-native-options/VERIFY.json",
                  "runs/24-native-options/api-exceptions-fixed/VERIFY.json",
                  "runs/24-native-options/api-exceptions-fixed/<режим>/stdout.log"],
        "recommendation": "Проверять версию нативного бэкенда и значения параметров до сравнения производительности.",
        "next": "На живом USVM проверить нативные default и переписывание при одинаковой клиентской кодировке.",
    })
    build(load(ROOT / "current.json")["current"])


def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    prior = load(CONFIRM)["arms"]
    assert all(prior[arm]["exact_vector_passed"] for arm in ARMS)
    assert not prior["C05"]["exact_vector_passed"]
    fresh_source = FRESH / "independent-manifest.json"
    artifacts = []
    if fresh_source.exists():
        by_project = defaultdict(list)
        for session in load(fresh_source)["sessions"]:
            by_project[session["family"]].append(session["tag"])
        for a in load(FRESH_SCOPE)["artifacts"]:
            if len(by_project[a["project"]]) < 3:
                continue
            artifacts.append({**a, "tags": by_project[a["project"]]})
    scenario = "new Java project tags recorded for portable rewrite validation"
    source = fresh_source
    if len(artifacts) < 3:
        source = OLD_LIVE
        artifacts = load(OLD_LIVE)["configurations"]
        scenario = "reused method tags from the earlier live-client experiment"
    configs = [{**a, "tags": sorted(set(a["tags"]), key=lambda x: hashlib.sha256(
                    ("native-rewrite\0"+x).encode()).hexdigest())[:6]} for a in artifacts]
    assert len(configs) >= 3
    verified = BASE / "compile/VERIFY.json"
    scope = {
        "locked": now(), "hypothesis": "Do the supported exact rewriting settings improve wall time without worse empirical coverage on the live USVM client?",
        "native_library": prior["default"]["version"], "guards": {"jvm_case_s": JVM_CAP, "method_ms": 6000, "check_ms": 1600},
        "version_probe_sha256": digest(CONFIRM), "option_only_overlay_sha256": digest(verified),
        "source": str(source.relative_to(ROOT)), "method_source_sha256": digest(source),
        "source_kind": scenario, "artifacts": configs, "repeats": R, "batch_size": SIZE,
        "options": {a: settings()[a] for a in ARMS},
        "design": "Exact same installed JNI solver and TableIte client, common option-check overlay, matched JVM batch and physical CPU, randomized order. Pick method tags by fixed hash from Z3-capture admission, never by cvc5 performance.",
        "outcomes": "Paired wall and coverage on jointly emitted ok methods. All intended methods, missing, JVM timeout, error, distinct steps separately. Whole-project bootstrap.",
    }
    write_json(SCOPE, scope)
    SCOPE.chmod(0o444)
    return scope


def args(options):
    return ",".join(k+"="+v for k, v in options.items())


def run_case(folder, plan, artifact, tags, repeat, core, arm, cap=None):
    cap=JVM_CAP if cap is None else cap
    native = args(plan["options"][arm])
    if not (folder / "status.json").exists():
        one_case(folder, artifact, tags, repeat, core, arm, cap,
                 classpath_overlay=OVERLAY,
                 extra_env=("NIGHT_CVC5_OPTIONS="+native,), solver_config=("cvc5", True))
    doc = load(folder / "status.json")
    assert doc["requested"] == tags and doc["core"] == core and doc["repeat"] == repeat and doc["arm"] == arm
    journal = (folder / "java.log").read_text()
    check = f"[night-native-options] {plan['native_library']}|{native}"
    marker = check in journal.splitlines()
    obs = folder / "results.jsonl"
    rows = {r["tag"]: r for r in lines(obs)} if obs.exists() else {}
    assert len(rows) == doc["rows"] and set(rows) <= set(tags)
    assert marker or not any(r["status"] == "ok" for r in rows.values())
    return doc, rows, marker


def smoke(plan):
    folder = BASE / "smoke"
    gate = folder / "VERIFY.json"
    if gate.exists():
        assert load(gate)["ok"]
        return
    item = plan["artifacts"][0]
    tags = item["tags"][:2]
    comparison = {}
    for arm in ARMS:
        doc, rows, record = run_case(folder / arm, plan, item, tags, 0, CORES[0], arm, 55)
        comparison[arm] = {"marker": record, "ok": doc["rc"] == 0 and record and len(rows) == 2 and
                           all(r["status"] == "ok" and r["stats"]["checks"] for r in rows.values())}
    checked = {"ok": all(x["ok"] for x in comparison.values()), "programs": len(tags), "repeats": 1, "arms": comparison}
    write_json(gate, checked)
    assert checked["ok"], comparison


def preflight():
    """Exercise the overlay on two already disclosed methods before the new run."""
    compile_overlay()
    old = load(OLD_LIVE)["configurations"]
    artifact = next((a for a in old if a["project"] == "commons-cli"), old[0])
    chosen = artifact["tags"][:2]
    plan = {"options": settings(), "native_library": load(CONFIRM)["arms"]["default"]["version"]}
    folder = BASE / "overlay-preflight"
    results = {}
    for arm in ARMS:
        status, rows, verified = run_case(folder / arm, plan, artifact, chosen, 0, 14, arm, 45)
        results[arm] = {"marked": verified, "status": status, "method_status": {t: x["status"] for t, x in rows.items()}}
    write_json(folder / "VERIFY.json", {"at": now(), "programs": len(chosen), "repeats": 1, "results": results})
    assert all(x["marked"] and x["status"]["rc"] == 0 and set(x["method_status"].values()) == {"ok"}
               and len(x["method_status"]) == len(chosen) for x in results.values())


def blocks(plan):
    for index, art in enumerate(plan["artifacts"]):
        for offset in range(0, len(art["tags"]), SIZE):
            for repeat in range(R):
                part = offset//SIZE
                core = CORES[(index + 2*part + repeat) % len(CORES)]
                rng = random.Random(219031 + index*16381 + part*619 + repeat*43)
                tags = art["tags"][offset:offset+SIZE]
                rng.shuffle(tags)
                order = rng.sample(ARMS, len(ARMS))
                folder = BASE / art["project"] / f"batch-{part}-repeat-{repeat}"
                yield folder, art, tags, repeat, core, order


def measure(core, plan):
    os.sched_setaffinity(0, {core})
    for folder, art, tags, repeat, assigned, order in blocks(plan):
        if assigned != core:
            continue
        assert STOP-time.time() > 2*JVM_CAP+12
        folder.mkdir(exist_ok=True, parents=True)
        write_json(folder / "order.json", {"tags": tags, "arms": order, "cpu": assigned})
        for arm in order:
            run_case(folder / arm, plan, art, tags, repeat, assigned, arm)


def analyse(plan):
    counters = {a: Counter() for a in ARMS}
    by_tag = defaultdict(list)
    projects = {}
    hashes = {}
    for folder, art, tags, repeat, core, order in blocks(plan):
        assert load(folder / "order.json") == {"tags": tags, "arms": order, "cpu": core}
        observations = {}
        for a in order:
            doc, rows, marker = run_case(folder / a, plan, art, tags, repeat, core, a)
            c = counters[a]
            c["jvm_timeout"] += doc["jvm_timeout"]
            c["jvm_error"] += bool(doc["rc"]) and not doc["jvm_timeout"]
            c["jvm_with_verified_options"] += marker
            observed = folder / a / "results.jsonl"
            if observed.exists():
                hashes[str(observed.relative_to(ROOT))] = digest(observed)
            for tag in tags:
                c["missing" if tag not in rows else rows[tag]["status"]] += 1
            observations[a] = rows
        for tag in tags:
            projects[tag] = art["project"]
            pair = [observations[a].get(tag) for a in ARMS]
            if any(row is None or row["status"] != "ok" for row in pair):
                continue
            x, y = pair
            for row in pair:
                assert row["wall_ms"] > 0 and 0 <= row["stats"]["coverage_pct"] <= 100
            by_tag[tag].append([x["wall_ms"], y["wall_ms"], x["stats"]["coverage_pct"],
                                y["stats"]["coverage_pct"], x["stats"]["check_ms"], y["stats"]["check_ms"],
                                x["stats"]["steps"], y["stats"]["steps"]])
    tags = sorted(by_tag)
    n = len(projects)
    assert all(c["missing"]+c["ok"]+sum(v for k,v in c.items() if k.startswith("error:")) == n*R
               for c in counters.values())
    values = np.asarray([np.mean(by_tag[tag], axis=0) for tag in tags])
    draw = group_draws([projects[tag] for tag in tags], np.random.default_rng(729415))
    cov = values[:, 3] - values[:, 2]
    result = {
        "version": plan["native_library"], "programs": n, "projects": len(set(projects.values())), "repeats": R,
        "planned_per_arm": n*R, "counters": counters, "paired_methods": len(tags),
        "paired_runs": sum(len(v) for v in by_tag.values()), "raw_hashes": hashes,
        "wall_ratio": frac(values[:, 1], values[:, 0], draw),
        "check_ratio": frac(values[:, 5], values[:, 4], draw) if np.all(draw @ values[:, 4] > 0) else None,
        "steps_ratio": frac(values[:, 7], values[:, 6], draw) if np.all(draw @ values[:, 6] > 0) else None,
        "coverage_difference_pp": [float(cov.mean()), np.percentile((draw@cov)/draw.sum(axis=1), [2.5, 97.5]).tolist()],
        "per_method": {tag: {"project": projects[tag], "pairs": len(by_tag[tag]),
                             "averages": values[i].tolist()} for i, tag in enumerate(tags)},
    }
    write_json(BASE / "VERIFY.json", result)
    return result


def publish(plan, result):
    n, k, paired = result["programs"], result["projects"], result["paired_runs"]
    summary = f"В живом USVM нативное переписывание дало отношение полного времени {fmt_ci(result['wall_ratio'])} на {paired} завершённых парах."
    write_json(ROOT / "evidence/25-native-rewrite.json", {
        "title": "25 · Нативное переписывание на живом USVM",
        "done": f"Проверено сравнение режимов cvc5 в живом USVM на {n} методах.",
        "summary": summary,
        "paragraphs": [
            f"{n} методов, {k} JAR, {R} повторов, по {result['planned_per_arm']} запланированных вызовов на режим. Источник выбора методов указан в протоколе. Каждое исполнение заново проходит символические пути.",
            f"В обеих ветках cvc5 {result['version']} через один JNI и одна замена таблиц на ite. На старом бэкенде флаги переписывания меняют simplification с batch на none. Применение проверено через getOption перед проверками. Оригинальный клиент сохранён, локальный класс добавляет только установку и запись параметров.",
            f"Разность покрытия на завершённых парах {fmt_ci(result['coverage_difference_pp'])} п.п. ДИ по целым проектам. Внутренние проверки уравняли на 1,6 с, метод ограничен 6 с. Ошибки и внешние отсечения JVM учтены отдельно.",
            (f"Отношение суммарного времени проверок по paired ok {fmt_ci(result['check_ratio'])}." if result["check_ratio"] else
             "Для отношения суммарного времени проверок недостаточно пригодных отметок в некоторых проектных блоках."),
        ],
        "tables": [card_table(f"Полный USVM · {result['planned_per_arm']} вызовов на режим",
            ["Режим", "Статус ok", "Нет записи", "Ошибки методов", "Таймаут JVM", "Прочие ошибки JVM", "JVM с подтверждением флагов"],
            [[arm, row["ok"], row["missing"], sum(v for k,v in row.items() if k.startswith("error:")),
              row["jvm_timeout"], row["jvm_error"], row["jvm_with_verified_options"]]
             for arm, row in result["counters"].items()])],
        "paths": ["protocols/native-rewrite.json", "runs/25-native-rewrite/compile/VERIFY.json",
                  "runs/25-native-rewrite/smoke/VERIFY.json", "runs/25-native-rewrite/VERIFY.json",
                  "runs/25-native-rewrite/<проект>/<серия>/<режим>/java.log"],
        "recommendation": "Сверять сквозное время и покрытие при обновлении реальной нативной версии.",
        "next": "Проверить те же контрасты в живом клиенте на cvc5 1.4.1 отдельно от версии JNI из установленного KSMT.",
    })
    build()


def main():
    compile_overlay()
    plan = freeze()
    smoke(plan)
    with ProcessPoolExecutor(max_workers=len(CORES)) as pool:
        tasks = [pool.submit(measure, core, plan) for core in CORES]
        for task in tasks:
            task.result(timeout=max(1, min(1200, STOP-time.time()-20)))
    publish(plan, analyse(plan))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "compatibility":
        report_compatibility()
    elif len(sys.argv) > 1 and sys.argv[1] == "preflight":
        preflight()
    else:
        main()
