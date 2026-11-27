"""Independently check sampled scalar values from SAT models at their query scopes."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import os
import random
import re
import subprocess
import time

from arrays_options import SCOPE as OPTIONS
from replay import CAMPAIGN, CORES, ROOT, STOP, append, check_budget, digest, ev, guarded_popen, lines, load, now
from report import build, write_json
from stability_metrics import card_table
from validate_cores import cvc_capture, render, terms

BASE = ROOT / "runs/26-model-witnesses"
SCOPE = ROOT / "protocols/model-witnesses.json"
QUERY = {"check-sat", "check-sat-assuming"}
MARKERS = re.compile(r'(?m)^"?night_values_(\d+)_([ab])"?\s*$')
CAP = 3.0
N = 12
TARGETS = 3
VALUES = 16
SOURCES = {
    "Генераторный портфель": ("runs/02-portfolio/test-corpus/manifest.json", False),
    "Первый переносимый библиотечный набор": ("runs/12-new-libraries/portable-corpus/manifest.json", True),
    "Другие библиотеки": ("runs/23-rewrite-transfer/independent-manifest.json", True),
}


def atom_set(expr):
    if isinstance(expr, str):
        return {expr}
    return set().union(*(atom_set(x) for x in expr))


def scalar_signature(t):
    if t[0] == "declare-fun" and len(t) == 4 and t[2] == []:
        return render(t[3])
    if t[0] == "declare-const" and len(t) == 3:
        return render(t[2])
    return None


def inspect(item, number, write=False):
    src = ROOT / item["path"]
    original = src.read_text()
    assert digest(src) == item["sha256"] and "night_values_" not in original
    ast = terms(original)
    context, declarations, active_claims = [], [{}], [[]]
    previous_check = None
    model_targets = {}
    assembled = []
    answers_seen = 0
    rich_definitions = False
    scalars = re.compile(r"^(Bool|Int|\(_ BitVec [1-9][0-9]*\))$")
    for index, clause in enumerate(ast):
        op = clause[0]
        if op in QUERY:
            answers_seen += 1
            previous_check = (clause, item["oracle"][answers_seen - 1])
        if op == "get-model" and previous_check is not None and previous_check[1] == "sat":
            declared = {name: kind for layer in declarations for name, kind in layer.items()}
            constraints = [term for layer in active_claims for term in layer]
            assumption = previous_check[0][1] if previous_check[0][0] == "check-sat-assuming" else []
            workload = constraints + assumption
            symbols = atom_set(workload)
            relevant = symbols & set(declared)
            possible = sorted(name for name in relevant if declared[name] and scalars.fullmatch(declared[name]))
            if possible:
                selected = sorted(random.Random(62261+number*101+index).sample(possible, min(len(possible), VALUES)))
                model_targets[index] = {
                    "declared": selected, "context": context.copy(), "query": render(previous_check[0]),
                    "query_no": answers_seen, "formula": render(["and", *workload]),
                    "scalar_complete": set(selected) == relevant and not rich_definitions,
                }
        if op in ("declare-fun", "declare-const"):
            declarations[-1][clause[1]] = scalar_signature(clause)
        if op in ("define-fun", "define-funs-rec", "define-fun-rec", "declare-datatypes", "declare-datatype"):
            rich_definitions = True
        if op == "assert":
            active_claims[-1].append(clause[1])
        if op == "push":
            assert len(clause) == 2 and clause[1] == "1"
            declarations.append({})
            active_claims.append([])
        if op == "pop":
            assert len(clause) == 2 and clause[1] == "1" and len(declarations) > 1
            declarations.pop()
            active_claims.pop()
        assert op not in ("reset", "reset-assertions")
        if op == "set-option" and clause[1] in (":global-declarations", ":global-decls"):
            assert clause[2] == "false"
        if op not in QUERY and not op.startswith("get-") and op not in ("echo", "exit"):
            if op != "set-option" or not clause[1].startswith(":produce-"):
                context.append(render(clause))
        assembled.append(render(clause))
    assert answers_seen == len(item["oracle"])
    if not write:
        return bool(model_targets)
    choices = sorted(random.Random(number+58247).sample(list(model_targets), min(len(model_targets), TARGETS)))
    wanted = {i: model_targets[i] for i in choices}
    out = BASE / "instrumented" / f"{number:03d}.smt2"
    out.parent.mkdir(exist_ok=True)
    with out.open("w") as f:
        for i, command in enumerate(assembled):
            f.write(command+"\n")
            if i in wanted:
                variables = render(wanted[i]["declared"])
                f.write(f'(echo "night_values_{i}_a")\n(get-value {variables})\n(echo "night_values_{i}_b")\n')
    return out, wanted


def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    options = load(OPTIONS)["configs"]
    configs = {"default": {}, "C05": options["global"], "rewrite": options["global_rewrite"]}
    accepted, excludes = [], {}
    seen = set()
    for provenance, (file, arrays) in SOURCES.items():
        manifest = ROOT / file
        if not manifest.exists() and provenance == "Другие библиотеки":
            manifest = ROOT / "runs/17-arrays-options/portable/manifest.json"
        choices = sorted(load(manifest)["sessions"], key=lambda r: r["sha256"])[:24]
        exclude = excludes[provenance] = Counter()
        found = 0
        for row in choices:
            if found == N:
                break
            if row["sha256"] in seen:
                exclude["duplicate"] += 1
                continue
            item = {"path": os.path.relpath(manifest.parent / row["file"], ROOT),
                    "sha256": row["sha256"], "oracle": row["oracle"], "group": row["group"],
                    "project": row.get("family", row["group"]), "arrays_exp": arrays,
                    "source": str(manifest.relative_to(ROOT)), "provenance": provenance}
            try:
                eligible = inspect(item, found)
            except (ValueError, AssertionError, IndexError):
                exclude["unsupported_static_scope"] += 1
                continue
            if not eligible:
                exclude["no_scalar_model_target"] += 1
                continue
            accepted.append(item)
            found += 1
            seen.add(row["sha256"])
    assert len(accepted) >= 12
    plan = {"locked": now(), "inputs": accepted, "excluded_before_solver": excludes,
            "configs": configs, "targets_per_program": TARGETS, "chosen_scalars_per_query": VALUES,
            "repeats": 1, "cvc5_cap_s": 4.5, "z3_cap_s": CAP,
            "scope": "Reuse fixed transcripts, hash-select programs and SAT model requests before looking at solver values. Check sample of scalar values for compatibility with original assertions and assumptions, and when complete check implication independently.",
            "limitations": "No generic interpretation validation for arrays, symbols outside the sampled values, or floating-point values. Correctness probe only; unmatched targets remain explicit."}
    write_json(SCOPE, plan)
    SCOPE.chmod(0o444)
    return plan


def decode(stdout, wanted):
    found = {}
    markers = list(MARKERS.finditer(stdout))
    for a, b in zip(markers, markers[1:]):
        if a[2] == "a" and b[2] == "b" and a[1] == b[1] and int(a[1]) in wanted:
            found[int(a[1])] = stdout[a.end():b.start()].strip()
    return found


def z3_check(path, clauses, terminal):
    script = path.with_suffix(".smt2")
    script.write_text("\n".join([*clauses, terminal, "(exit)"])+"\n")
    solver = CAMPAIGN / ".venv/bin/z3"
    cap = min(CAP, max(.01, STOP-time.time()-8))
    with guarded_popen(["/usr/bin/prlimit", "--as=2147483648", "--core=0", "--", solver,
                        "-smt2", "-T:2", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL) as p:
        try:
            out, err = p.communicate(timeout=cap)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
            returncode = "wall-timeout"
        else:
            returncode = p.returncode
    path.with_suffix(".stdout").write_bytes(out)
    path.with_suffix(".stderr").write_bytes(err)
    answer = out.strip().decode(errors="replace")
    if returncode == "wall-timeout" or answer == "timeout":
        status = "timeout"
    elif returncode or answer not in ("sat", "unsat", "unknown"):
        status = "error"
    else:
        status = answer
    return {"status": status, "returncode": returncode, "script_sha256": digest(script)}


def check_values(directory, i, item, printed):
    result = {"query": item["query_no"], "values": len(item["declared"])}
    if printed is None:
        return {**result, "status": "unobserved"}
    try:
        expressions = terms(printed)
        assert len(expressions) == 1
        assignments = expressions[0]
        assert [key for key, _ in assignments] == item["declared"]
    except (ValueError, AssertionError, TypeError):
        return {**result, "status": "malformed", "printed": printed}
    stabilizers = [render(["assert", ["=", key, value]]) for key, value in assignments]
    compatible = z3_check(directory / f"{i}-fits", [*item["context"], *stabilizers], item["query"])
    result.update(status=compatible["status"], compatibility=compatible)
    if compatible["status"] == "sat" and item["scalar_complete"]:
        empty = [line for line in item["context"] if not line.startswith("(assert ")]
        proof = z3_check(directory / f"{i}-implies", [*empty, *stabilizers, "(assert (not "+item["formula"]+"))"], "(check-sat)")
        result["entailment"] = proof
    return result


def worker(slot, plan):
    core = CORES[slot]
    os.sched_setaffinity(0, {core})
    journal = BASE / f"worker-{slot}.jsonl"
    assert not journal.exists()
    for number in range(slot, len(plan["inputs"]), len(CORES)):
        source = plan["inputs"][number]
        program, wanted = inspect(source, number, write=True)
        order = list(plan["configs"])
        random.Random(297903+number).shuffle(order)
        for position, arm in enumerate(order):
            check_budget()
            result = cvc_capture(program, plan["configs"][arm], source["arrays_exp"])
            folder = BASE / f"case-{number:03d}" / arm
            folder.mkdir(parents=True)
            (folder/"cvc5.stdout").write_text(result["stdout"])
            (folder/"cvc5.stderr").write_text(result["stderr"])
            actual = ev.ANSWERS.findall(result["stdout"])
            seen = decode(result["stdout"], wanted)
            values = [check_values(folder, i, info, seen.get(i)) for i, info in wanted.items()]
            append(journal, {"program": number, "arm": arm, "core": core, "position": position,
                             "source_sha256": source["sha256"], "cvc5_status": result["status"],
                             "cvc5_output_sha256": digest(folder/"cvc5.stdout"), "answers": actual,
                             "answers_match": actual == source["oracle"][:len(actual)] and
                             (result["status"] != "solved" or len(actual) == len(source["oracle"])),
                             "model_checks": values})


def audit(plan):
    rows = [row for slot in range(len(CORES)) for row in lines(BASE / f"worker-{slot}.jsonl")]
    assert len({(row["program"], row["arm"]) for row in rows}) == len(rows) == len(plan["inputs"])*len(plan["configs"])
    stats = {arm: {"value_compatibility": Counter(), "scalar_entailment": Counter(), "sessions": Counter()}
             for arm in plan["configs"]}
    for row in rows:
        n = row["program"]
        case = plan["inputs"][n]
        assert case["sha256"] == row["source_sha256"]
        order = list(plan["configs"])
        random.Random(297903+n).shuffle(order)
        arm = row["arm"]
        assert arm == order[row["position"]] and row["core"] == CORES[n % len(CORES)]
        folder = BASE / f"case-{n:03d}" / arm
        assert digest(folder/"cvc5.stdout") == row["cvc5_output_sha256"]
        assert ev.ANSWERS.findall((folder/"cvc5.stdout").read_text()) == row["answers"]
        target = stats[arm]
        target["sessions"][row["cvc5_status"]] += 1
        expected = case["oracle"]
        correct = row["answers"] == expected[:len(row["answers"])]
        if row["cvc5_status"] == "solved":
            correct = correct and len(row["answers"]) == len(expected)
        assert row["answers_match"] == correct
        target["answer_mismatch"] = target.get("answer_mismatch", 0)+int(not correct)
        for result in row["model_checks"]:
            assert case["oracle"][result["query"]-1] == "sat"
            target["value_compatibility"][result["status"]] += 1
            if "entailment" in result:
                target["scalar_entailment"][result["entailment"]["status"]] += 1
    result = {"programs": len(plan["inputs"]), "repeats": 1, "rows": len(rows),
              "provenance": Counter(row["provenance"] for row in plan["inputs"]), "modes": stats,
              "scope_sha256": digest(SCOPE)}
    write_json(BASE/"VERIFY.json", result)
    return result


def publish(result):
    n = result["programs"]
    models = result["modes"]
    total = sum(sum(r["value_compatibility"].values()) for r in models.values())
    sat = sum(r["value_compatibility"].get("sat", 0) for r in models.values())
    bad = sum(r["value_compatibility"].get("unsat", 0) for r in models.values())
    complete_scalar = sum(sum(r["scalar_entailment"].values()) for r in models.values())
    summary = f"Z3 подтвердил совместимость {sat} наборов скалярных значений из {total} запланированных запросов. Несовместимых {bad}."
    write_json(ROOT/"evidence/26-model-witnesses.json", {
        "title": "26 · Проверка скалярных значений SAT-моделей",
        "done": f"Проверены полученные значения моделей на {n} полных сеансах.",
        "summary": summary,
        "paragraphs": [
            f"{n} ранее записанных сеансов из {len(result['provenance'])} источников, по одному инструментированному повтору на режим. До трёх исходных запросов модели и до 16 используемых скалярных переменных на проверку. Выбор исходов зафиксирован по хешу и позиции запроса.",
            f"Значения получены отдельным SMT-LIB get-value после штатного get-model. Независимый Z3 решает исходную проверку вместе с равенствами переменных возвращённым значениям. Полная проверка следования активных ограничений была доступна для {complete_scalar} запросов, у которых значения покрыли все используемые объявления.",
            "Проверка распространяется на выбранные Bool, Int и BitVec. Частичная совместимость допускает другие назначения массивов и функций. Таймауты, ошибки разбора и недошедшие запросы выделены.",
        ],
        "tables": [card_table("Независимая проверка выбранных SAT-моделей · один повтор на режим",
                   ["Режим", "Целей", "Совместимы", "Несовместимы", "Не получены", "Таймаут/ошибка/unknown",
                    "Полных скалярных проверок", "Следование подтверждено", "Расхождения ответов"],
                   [[arm, sum(p["value_compatibility"].values()), p["value_compatibility"].get("sat", 0),
                     p["value_compatibility"].get("unsat", 0), p["value_compatibility"].get("unobserved", 0),
                     sum(p["value_compatibility"].get(key, 0) for key in ("timeout", "error", "unknown", "malformed")),
                     sum(p["scalar_entailment"].values()), p["scalar_entailment"].get("unsat", 0), p["answer_mismatch"]]
                    for arm, p in models.items()])],
        "paths": ["protocols/model-witnesses.json", "runs/26-model-witnesses/VERIFY.json",
                  "runs/26-model-witnesses/worker-{0..5}.jsonl", "runs/26-model-witnesses/case-<номер>/<режим>/"],
        "recommendation": "Сохранять независимую сверку значений и ядер при переносе настроек на новый реальный клиент.",
        "next": "Для клиентских случаев с массивами отдельно проверить наблюдаемые значения select и фактическую реконструкцию объекта.",
    })
    build()


def main():
    BASE.mkdir(exist_ok=True)
    plan = freeze()
    if not (BASE/"VERIFY.json").exists():
        controls = BASE/"smoke"
        controls.mkdir(exist_ok=True)
        original = os.sched_getaffinity(0)
        try:
            os.sched_setaffinity(0, {CORES[0]})
            common = ["(set-logic QF_BV)", "(declare-const x (_ BitVec 8))", "(assert (= x #x02))"]
            passed = z3_check(controls/"correct", [*common, "(assert (= x #x02))"], "(check-sat)")
            rejected = z3_check(controls/"wrong", [*common, "(assert (= x #x03))"], "(check-sat)")
        finally:
            os.sched_setaffinity(0, original)
        write_json(controls/"VERIFY.json", {"correct": passed, "wrong": rejected})
        assert passed["status"] == "sat" and rejected["status"] == "unsat"
        with ProcessPoolExecutor(max_workers=len(CORES)) as pool:
            pending = [pool.submit(worker, slot, plan) for slot in range(len(CORES))]
            for future in pending:
                future.result(timeout=max(1, min(700, STOP-time.time()-12)))
    publish(audit(plan))


if __name__ == "__main__":
    main()
