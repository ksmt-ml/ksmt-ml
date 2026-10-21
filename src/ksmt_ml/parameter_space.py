"""Build and qualify the cvc5 1.3.4 regular-bv parameter space."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from ksmt_ml.evaluator import (
    CANDIDATE_SCHEMA_VERSION,
    EvaluatorError,
    load_json,
    normalize_candidate,
    run_replay,
    validate_parameter_space,
    write_json,
)


INVENTORY_SCHEMA_VERSION = "cvc5-option-inventory-v0"
RUNTIME_INVENTORY_SCHEMA_VERSION = "tsa-cvc5-option-inventory-v0"
SMOKE_REPORT_SCHEMA_VERSION = "cvc5-parameter-space-smoke-report-v0"
INVENTORY_ID = "cvc5-1.3.4-runtime-v0"
PARAMETER_SPACE_ID = "cvc5-1.3.4-regular-bv-wide-v0"

ACTIVE_MODULES = frozenset(
    {
        "Arrays Theory Module",
        "Bitvector Theory Module",
        "Decision Heuristics Module",
        "SAT Layer Module",
        "SMT Layer Module",
        "Theory Layer Module",
        "Uninterpreted Functions Theory Module",
    }
)

THEORY_GATED_MODULES = frozenset(
    {
        "Arithmetic Theory Module",
        "Bags Theory Module",
        "Datatypes Theory Module",
        "Finite Field Theory Module",
        "Floating-Point Module",
        "Quantifiers Module",
        "Separation Logic Theory Module",
        "Sets Theory Module",
        "Strings Theory Module",
    }
)

EVALUATOR_CONTROLLED = frozenset(
    {
        "incremental",
        "rlimit",
        "rlimit-per",
        "seed",
        "sat-random-seed",
        "tlimit",
        "tlimit-per",
        "produce-models",
        "produce-unsat-cores",
        "arrays-exp",
        "fp-exp",
    }
)

COMMON_ACTIVE = frozenset(
    {"decision", "condense-function-values", "default-function-value-mode"}
)

INFRASTRUCTURE_OPTIONS = frozenset(
    {
        "minisat-dump-dimacs",
        "difficulty-mode",
        "interpolants-mode",
        "minimal-unsat-cores",
        "output-print-defs",
        "print-cores-full",
        "produce-abducts",
        "produce-assertions",
        "produce-assignments",
        "produce-difficulty",
        "produce-interpolants",
        "produce-learned-literals",
        "proof-mode",
        "timeout-core-timeout",
        "unsat-cores-mode",
    }
)

THEORY_GATED_OPTIONS = frozenset(
    {
        "nl-ext",
        "cbqi",
        "enum-inst",
        "full-saturate-quant",
        "solve-int-as-bv",
        "solve-real-as-int",
        "uf-ho-exp",
        "uf-ho-ext",
        "uf-lambda-qe",
    }
)

UNSAFE_OPTIONS = frozenset(
    {
        "ackermann",
        "arrays-weak-equiv",
        "bitblast",
        "bool-to-bv",
        "bv-rw-extend-eq",
        "deep-restart",
        "ite-simp",
        "learned-rewrite",
        "lemma-inprocess",
        "unconstrained-simp",
    }
)

INACTIVE_PARENT_OPTIONS = frozenset({"bv-sat-solver", "deep-restart-factor"})

NUMERIC_DOMAINS: dict[str, dict[str, Any]] = {
    "arrays-prop": {
        "kind": "integer",
        "minimum": 0,
        "maximum": 2,
        "step": 1,
        "sampling": "uniform",
    },
    "random-freq": {
        "kind": "number",
        "minimum": 0.0,
        "maximum": 1.0,
        "sampling": "uniform",
    },
    "restart-int-base": {
        "kind": "integer",
        "minimum": 1,
        "maximum": 10_000,
        "step": 1,
        "sampling": "log-uniform",
    },
    "restart-int-inc": {
        "kind": "number",
        "minimum": 0.0,
        "maximum": 1_000.0,
        "positiveMinimum": 0.01,
        "sampling": "log-uniform-or-zero",
    },
    "uf-ss-abort-card": {
        "kind": "integer",
        "minimum": -1,
        "maximum": 64,
        "step": 1,
        "sampling": "uniform",
    },
    "bvand-integer-granularity": {
        "kind": "integer",
        "minimum": 1,
        "maximum": 8,
        "step": 1,
        "sampling": "uniform",
    },
    "deep-restart-factor": {
        "kind": "number",
        "minimum": 0.0,
        "maximum": 1_000.0,
        "positiveMinimum": 0.01,
        "sampling": "log-uniform-or-zero",
    },
}

ACTIVE_WHEN: dict[str, dict[str, Any]] = {
    "bv-sat-solver": {"option": "bitblast", "equals": "eager"},
    "deep-restart-factor": {"not": {"option": "deep-restart", "equals": "none"}},
    "bv-to-int-use-pow2": {"not": {"option": "solve-bv-as-int", "equals": "off"}},
    "bvand-integer-granularity": {
        "not": {"option": "solve-bv-as-int", "equals": "off"}
    },
    "iand-mode": {"not": {"option": "solve-bv-as-int", "equals": "off"}},
    "minisat-simplification": {"option": "sat-solver", "equals": "minisat"},
    "random-freq": {"option": "sat-solver", "equals": "minisat"},
    "restart-int-base": {"option": "sat-solver", "equals": "minisat"},
    "restart-int-inc": {"option": "sat-solver", "equals": "minisat"},
    "uf-ss-abort-card": {"not": {"option": "uf-ss", "equals": "none"}},
    "uf-ss-fair": {"not": {"option": "uf-ss", "equals": "none"}},
    "uf-ss-fair-monotone": {"not": {"option": "uf-ss", "equals": "none"}},
}


class ParameterSpaceError(ValueError):
    pass


def parse_module_map(review_path: Path) -> dict[str, str]:
    text = review_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^### (.+?) — (\d+)\n\n`([^\n]*)`", re.MULTILINE)
    option_to_module: dict[str, str] = {}
    for match in pattern.finditer(text):
        module, expected_count, rendered = match.groups()
        options = [value.strip("`") for value in rendered.split("`, `")]
        if len(options) != int(expected_count):
            raise ParameterSpaceError(f"module count mismatch for {module}")
        for option in options:
            if option in option_to_module:
                raise ParameterSpaceError(f"option occurs in two modules: {option}")
            option_to_module[option] = module
    if len(option_to_module) != 518:
        raise ParameterSpaceError(
            f"expected 518 classified options, got {len(option_to_module)}"
        )
    return option_to_module


def _classification(name: str, module: str, kind: str, modes: Sequence[str]) -> tuple[str, str]:
    if name in UNSAFE_OPTIONS:
        return "unsafe", "Real TSA/cvc5 singleton replay terminated the JVM with SIGABRT."
    if name in INACTIVE_PARENT_OPTIONS:
        return "inactive-parent", "Its activating parent mode is unsafe for the TSA regular-bv workload."
    if name in EVALUATOR_CONTROLLED:
        return "evaluator-controlled", "Fixed by the replay and correctness protocol."
    if name in INFRASTRUCTURE_OPTIONS:
        return "infrastructure", "Controls output, proof/result production, or evaluator policy."
    if name in THEORY_GATED_OPTIONS or module in THEORY_GATED_MODULES:
        return "theory-gated", "The required theory is absent from regular-bv v0 workloads."
    if module == "Most Commonly-Used cvc5 Options" and name not in COMMON_ACTIVE:
        return "infrastructure", "Common option belongs to IO, diagnostics, resources, or another theory."
    if module not in ACTIVE_MODULES:
        return "infrastructure", "Module is outside the single-threaded Java API solver search."
    if kind in {"string", "void"}:
        return "infrastructure", "Option is not a finite solver-heuristic domain."
    if kind == "categorical" and len(modes) < 2:
        return "fixed-singleton", "Runtime exposes no alternative value."
    return "active", "Solver heuristic in an active regular-bv module."


def _typed_default(raw: Mapping[str, Any]) -> Any:
    value = raw.get("defaultValue")
    kind = raw["kind"]
    if kind == "boolean":
        if value not in {"true", "false"}:
            raise ParameterSpaceError(f"invalid Boolean default for {raw['name']}")
        return value == "true"
    if kind == "integer":
        return int(value)
    if kind == "number":
        number = float(value)
        if not math.isfinite(number):
            raise ParameterSpaceError(f"non-finite default for {raw['name']}")
        return number
    return value


def _domain(raw: Mapping[str, Any]) -> dict[str, Any]:
    name = raw["name"]
    kind = raw["kind"]
    default = _typed_default(raw)
    if kind == "boolean":
        domain: dict[str, Any] = {"kind": "boolean", "default": default}
    elif kind == "categorical":
        values = list(raw.get("modes", []))
        if default not in values:
            raise ParameterSpaceError(f"default mode is absent for {name}")
        domain = {"kind": "categorical", "values": values, "default": default}
    elif kind in {"integer", "number"}:
        if name not in NUMERIC_DOMAINS:
            raise ParameterSpaceError(f"active numeric option needs a curated domain: {name}")
        domain = dict(NUMERIC_DOMAINS[name])
        domain["default"] = default
        if default < domain["minimum"] or default > domain["maximum"]:
            raise ParameterSpaceError(f"default is outside curated domain for {name}")
    else:
        raise ParameterSpaceError(f"unsupported active option kind for {name}: {kind}")
    if name in ACTIVE_WHEN:
        domain["activeWhen"] = ACTIVE_WHEN[name]
    return domain


def build_artifacts(
    runtime_inventory: Mapping[str, Any], module_map: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if runtime_inventory.get("schemaVersion") != RUNTIME_INVENTORY_SCHEMA_VERSION:
        raise ParameterSpaceError("unsupported runtime inventory schema")
    if runtime_inventory.get("solverVersion") != "1.3.4":
        raise ParameterSpaceError("parameter-space v0 is frozen to cvc5 1.3.4")
    raw_options = runtime_inventory.get("options")
    if not isinstance(raw_options, list) or len(raw_options) != 518:
        raise ParameterSpaceError("runtime inventory must contain 518 options")
    runtime_names = {entry["name"] for entry in raw_options}
    if runtime_names != set(module_map):
        missing = sorted(runtime_names.symmetric_difference(module_map))
        raise ParameterSpaceError(f"runtime/review option mismatch: {missing[:5]}")

    options: list[dict[str, Any]] = []
    active_domains: dict[str, Any] = {}
    for raw in sorted(raw_options, key=lambda value: value["name"]):
        name = raw["name"]
        status, reason = _classification(
            name, module_map[name], raw["kind"], raw.get("modes", [])
        )
        item = dict(raw)
        item["module"] = module_map[name]
        item["status"] = status
        item["reason"] = reason
        if status == "active":
            item["domain"] = _domain(raw)
            active_domains[name] = item["domain"]
        options.append(item)

    inventory = {
        "schemaVersion": INVENTORY_SCHEMA_VERSION,
        "inventoryId": INVENTORY_ID,
        "targetSolver": "cvc5",
        "solverVersion": "1.3.4",
        "acceptedNameCount": runtime_inventory["acceptedNameCount"],
        "canonicalOptionCount": len(options),
        "statusCounts": dict(sorted(Counter(item["status"] for item in options).items())),
        "source": {
            "schemaVersion": runtime_inventory["schemaVersion"],
            "method": "Solver.getOptionNames/getOptionInfo through TSA and the KSMT-loaded native library",
            "officialDocumentation": "https://cvc5.github.io/docs/cvc5-1.3.4/options.html",
        },
        "options": options,
    }
    parameter_space = {
        "schemaVersion": "cvc5-parameter-space-v0",
        "parameterSpaceId": PARAMETER_SPACE_ID,
        "inventoryId": INVENTORY_ID,
        "targetSolver": "cvc5",
        "compatibleSolverVersions": ["1.3.4"],
        "applicationLifecycle": "fresh-solver",
        "backendRole": "regular-bv",
        "options": dict(sorted(active_domains.items())),
        "constraints": [],
    }
    validate_parameter_space(parameter_space)
    return inventory, parameter_space


def _activation_values(predicate: Mapping[str, Any]) -> dict[str, Any]:
    if "option" in predicate:
        if "equals" in predicate:
            return {predicate["option"]: predicate["equals"]}
        return {predicate["option"]: predicate["in"][0]}
    if "all" in predicate:
        result: dict[str, Any] = {}
        for child in predicate["all"]:
            result.update(_activation_values(child))
        return result
    if "any" in predicate:
        return _activation_values(predicate["any"][0])
    child = predicate["not"]
    if set(child) == {"option", "equals"}:
        raise ParameterSpaceError(
            f"smoke witness for negated predicate must be supplied by a non-default mode: {child}"
        )
    raise ParameterSpaceError("unsupported activation witness")


def _condition_witness(
    domain: Mapping[str, Any], parameter_space: Mapping[str, Any]
) -> dict[str, Any]:
    predicate = domain.get("activeWhen")
    if predicate is None:
        return {}
    if "not" in predicate and set(predicate["not"]) == {"option", "equals"}:
        leaf = predicate["not"]
        parent = parameter_space["options"][leaf["option"]]
        alternatives = [
            value for value in _representative_values(parent) if value != leaf["equals"]
        ]
        if not alternatives:
            raise ParameterSpaceError(f"no activation witness for {leaf['option']}")
        return {leaf["option"]: alternatives[0]}
    return _activation_values(predicate)


def _representative_values(domain: Mapping[str, Any]) -> list[Any]:
    kind = domain["kind"]
    default = domain["default"]
    if kind == "boolean":
        return [default, not default]
    if kind == "categorical":
        return [default] + [value for value in domain["values"] if value != default]
    minimum = domain["minimum"]
    maximum = domain["maximum"]
    if kind == "integer":
        step = domain.get("step", 1)
        midpoint = minimum + ((maximum - minimum) // (2 * step)) * step
        values = [default, minimum, midpoint, maximum]
    else:
        values = [default, minimum, maximum]
        if domain.get("sampling") == "log-uniform-or-zero":
            values.append(domain["positiveMinimum"])
        elif minimum > 0:
            values.append(math.sqrt(minimum * maximum))
    return list(dict.fromkeys(values))


def singleton_smoke_candidates(parameter_space: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for name, domain in sorted(parameter_space["options"].items()):
        witness = _condition_witness(domain, parameter_space)
        for value in _representative_values(domain):
            if value == domain["default"]:
                continue
            proposed = {**witness, name: value}
            options = {
                option: selected
                for option, selected in proposed.items()
                if selected != parameter_space["options"][option]["default"]
            }
            identity = {"option": name, "value": value, "activation": witness}
            candidate = {
                "schemaVersion": CANDIDATE_SCHEMA_VERSION,
                "candidateId": f"cvc5-singleton-v0-{_short_hash(identity)}",
                "parameterSpaceId": parameter_space["parameterSpaceId"],
                "targetSolver": "cvc5",
                "options": options,
            }
            normalize_candidate(candidate, parameter_space)
            candidates.append(candidate)
    unique = {candidate["candidateId"]: candidate for candidate in candidates}
    return list(unique.values())


def _short_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _smoke_profile_paths(arguments: argparse.Namespace) -> list[Path]:
    paths = [path.resolve() for path in arguments.profile]
    if arguments.profile_dir is not None:
        paths.extend(sorted(arguments.profile_dir.resolve().glob("*.json")))
    unique = {str(path): path for path in paths if path.is_file()}
    if not unique:
        raise ParameterSpaceError("smoke requires at least one profile")
    return [unique[key] for key in sorted(unique)]


def run_smoke(arguments: argparse.Namespace) -> dict[str, Any]:
    parameter_space = load_json(arguments.parameter_space)
    validate_parameter_space(parameter_space)
    candidates = singleton_smoke_candidates(parameter_space)
    if arguments.random_candidates:
        from ksmt_ml.search import RandomSearchStrategy

        strategy = RandomSearchStrategy(parameter_space, arguments.seed)
        candidates.extend(
            strategy.ask().candidate for _ in range(arguments.random_candidates)
        )
    if arguments.limit is not None:
        candidates = candidates[: arguments.limit]
    plan_sha256 = hashlib.sha256(
        json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    profile_paths = _smoke_profile_paths(arguments)
    provenance = {
        "parameterSpaceSha256": _sha256_file(arguments.parameter_space),
        "tsaCliJarSha256": _sha256_file(arguments.tsa_cli_jar),
        "smokePlanSha256": plan_sha256,
        "sourceProfiles": [
            {"name": path.name, "sha256": _sha256_file(path)} for path in profile_paths
        ],
    }

    report_path = arguments.output_dir / "smoke-report.json"
    if report_path.is_file():
        report = load_json(report_path)
        if report.get("schemaVersion") != SMOKE_REPORT_SCHEMA_VERSION:
            raise ParameterSpaceError("existing smoke report has another schema")
        if report.get("parameterSpaceId") != parameter_space["parameterSpaceId"]:
            raise ParameterSpaceError("existing smoke report belongs to another space")
        for key, value in provenance.items():
            previous = report.get(key)
            if previous is not None and previous != value:
                raise ParameterSpaceError(f"existing smoke report has different {key}")
            report[key] = value
    else:
        report = {
            "schemaVersion": SMOKE_REPORT_SCHEMA_VERSION,
            "parameterSpaceId": parameter_space["parameterSpaceId"],
            "solverVersion": parameter_space["compatibleSolverVersions"][0],
            "backendRole": parameter_space["backendRole"],
            **provenance,
            "startedAtEpochMillis": int(time.time() * 1000),
            "completedAtEpochMillis": None,
            "plannedCandidateCount": len(candidates),
            "passedCandidateCount": 0,
            "failedCandidateCount": 0,
            "results": [],
        }
    completed = {result["candidateId"] for result in report["results"]}
    replay_dir = arguments.output_dir / "replays"
    for index, candidate in enumerate(candidates, start=1):
        if candidate["candidateId"] in completed:
            continue
        print(
            f"[{index}/{len(candidates)}] real cvc5 smoke {candidate['candidateId']} "
            f"{candidate['options']}",
            flush=True,
        )
        replay_path = replay_dir / f"{candidate['candidateId']}.json"
        started = time.monotonic_ns()
        try:
            replay = run_replay(
                tsa_cli_jar=arguments.tsa_cli_jar,
                candidate=candidate,
                parameter_space=parameter_space,
                output_path=replay_path,
                profile_directory=arguments.profile_dir,
                profiles=arguments.profile,
                backend_role=parameter_space["backendRole"],
                repetitions=1,
                warmups=0,
                java=arguments.java,
                process_timeout_seconds=arguments.process_timeout_seconds,
                stream_child_output=not arguments.quiet_child_output,
                progress_interval_seconds=arguments.progress_interval_seconds,
            )
            results = replay["results"]
            wrong = sum(result["wrongAnswerCount"] for result in results)
            timeouts = sum(result["knownStatusTimeoutCount"] for result in results)
            failures = sum(result["outcome"] != "SUCCESS" for result in results)
            passed = wrong == 0 and timeouts == 0 and failures == 0
            failure_message = None if passed else "wrong answer, known-status timeout, or replay failure"
        except (EvaluatorError, OSError) as failure:
            passed = False
            wrong = 0
            timeouts = 0
            failures = 1
            failure_message = str(failure)
        report["results"].append(
            {
                "candidateId": candidate["candidateId"],
                "options": candidate["options"],
                "status": "PASS" if passed else "FAIL",
                "durationNanos": time.monotonic_ns() - started,
                "wrongAnswerCount": wrong,
                "knownStatusTimeoutCount": timeouts,
                "failedReplayCount": failures,
                "failureMessage": failure_message,
            }
        )
        report["passedCandidateCount"] += int(passed)
        report["failedCandidateCount"] += int(not passed)
        write_json(report_path, report)
    report["completedAtEpochMillis"] = int(time.time() * 1000)
    write_json(report_path, report)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build inventory and regular-bv parameter space")
    build.add_argument("--runtime-inventory", type=Path, required=True)
    build.add_argument("--review", type=Path, required=True)
    build.add_argument("--inventory-output", type=Path, required=True)
    build.add_argument("--parameter-space-output", type=Path, required=True)

    smoke = commands.add_parser("smoke", help="run singleton and random candidates on real cvc5")
    smoke.add_argument("--parameter-space", type=Path, required=True)
    smoke.add_argument("--tsa-cli-jar", type=Path, required=True)
    smoke.add_argument("--profile-dir", type=Path)
    smoke.add_argument("--profile", type=Path, action="append", default=[])
    smoke.add_argument("--output-dir", type=Path, required=True)
    smoke.add_argument("--random-candidates", type=int, default=0)
    smoke.add_argument("--seed", type=int, default=0)
    smoke.add_argument("--limit", type=int)
    smoke.add_argument("--process-timeout-seconds", type=float, default=120)
    smoke.add_argument("--progress-interval-seconds", type=int, default=15)
    smoke.add_argument("--quiet-child-output", action="store_true")
    smoke.add_argument("--java", default="java")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        if arguments.command == "build":
            inventory, parameter_space = build_artifacts(
                load_json(arguments.runtime_inventory), parse_module_map(arguments.review)
            )
            write_json(arguments.inventory_output, inventory)
            write_json(arguments.parameter_space_output, parameter_space)
            print(
                f"Built {inventory['canonicalOptionCount']}-option inventory; "
                f"active regular-bv options={len(parameter_space['options'])}",
                flush=True,
            )
        else:
            report = run_smoke(arguments)
            print(
                f"Smoke complete: pass={report['passedCandidateCount']}, "
                f"fail={report['failedCandidateCount']}",
                flush=True,
            )
            return int(report["failedCandidateCount"] > 0)
        return 0
    except (EvaluatorError, ParameterSpaceError, OSError) as failure:
        print(f"parameter-space command failed: {failure}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
