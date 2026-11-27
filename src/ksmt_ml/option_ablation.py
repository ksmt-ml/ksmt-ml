"""Prepare and execute exhaustive option-subset ablation experiments."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from ksmt_ml.evaluator import (
    DEFAULT_TIMEOUT_PENALTY,
    WORKLOAD_TOTAL_AGGREGATION,
    EvaluatorError,
    load_json,
    normalize_candidate,
    run_profile_replay_matrix,
    score_reports,
    sha256_file,
    sha256_json,
    validate_baseline_manifest,
    validate_report_against_workload,
    validate_score,
    write_json,
)


ABLATION_MANIFEST_SCHEMA_VERSION = "cvc5-option-subset-ablation-v0"
ABLATION_RESULTS_SCHEMA_VERSION = "cvc5-option-subset-ablation-results-v0"


def prepare_option_subset_ablation(
    *,
    source_candidate_path: Path,
    parameter_space_path: Path,
    output_directory: Path,
) -> dict[str, Any]:
    """Materialize every subset of the source candidate's explicit options."""

    source_candidate_path = source_candidate_path.resolve()
    parameter_space_path = parameter_space_path.resolve()
    source = load_json(source_candidate_path)
    parameter_space = load_json(parameter_space_path)
    normalize_candidate(source, parameter_space)
    source_options = source["options"]
    if not source_options:
        raise EvaluatorError("source candidate has no options to ablate")
    option_names = sorted(source_options)
    if len(option_names) > 12:
        raise EvaluatorError("refusing exhaustive ablation of more than 12 options")

    output_directory = output_directory.resolve()
    candidates_directory = output_directory / "candidates"
    candidates_directory.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for mask in range(1 << len(option_names)):
        selected_names = [
            name for index, name in enumerate(option_names) if mask & (1 << index)
        ]
        options = {name: source_options[name] for name in selected_names}
        identity = {
            "parameterSpaceId": source["parameterSpaceId"],
            "sourceCandidateId": source["candidateId"],
            "options": options,
        }
        candidate_id = f"cvc5-ablation-v0-{sha256_json(identity)[:20]}"
        candidate = {
            "schemaVersion": source["schemaVersion"],
            "candidateId": candidate_id,
            "parameterSpaceId": source["parameterSpaceId"],
            "targetSolver": source["targetSolver"],
            "options": options,
        }
        normalize_candidate(candidate, parameter_space)
        candidate_path = candidates_directory / f"subset-{mask:02d}-v0.json"
        _write_or_verify(candidate_path, candidate, "ablation candidate")
        entries.append(
            {
                "ordinal": mask,
                "mask": mask,
                "optionNames": selected_names,
                "candidateId": candidate_id,
                "path": str(candidate_path.relative_to(output_directory)),
                "sha256": sha256_file(candidate_path),
            }
        )

    manifest = {
        "schemaVersion": ABLATION_MANIFEST_SCHEMA_VERSION,
        "sourceCandidate": {
            "candidateId": source["candidateId"],
            "fileName": source_candidate_path.name,
            "sha256": sha256_file(source_candidate_path),
        },
        "parameterSpace": {
            "parameterSpaceId": parameter_space["parameterSpaceId"],
            "fileName": parameter_space_path.name,
            "sha256": sha256_file(parameter_space_path),
        },
        "policy": {
            "kind": "exhaustive-explicit-option-subsets-v0",
            "optionNames": option_names,
            "candidateCount": len(entries),
        },
        "candidates": entries,
    }
    _write_or_verify(output_directory / "manifest-v0.json", manifest, "ablation manifest")
    return manifest


def run_option_subset_ablation(
    *,
    manifest_path: Path,
    baseline_manifest_path: Path,
    parameter_space_path: Path,
    tsa_cli_jar: Path,
    output_directory: Path,
    repetitions: int = 1,
    warmups: int = 0,
    matrix_timeout_seconds: float | None = None,
    trial_timeout_seconds: float | None = None,
    progress_interval_seconds: int = 15,
    timeout_penalty: float = DEFAULT_TIMEOUT_PENALTY,
    eligibility_results_path: Path | None = None,
) -> dict[str, Any]:
    """Replay every subset, reusing valid reports after interruption."""

    if repetitions <= 0:
        raise EvaluatorError("ablation repetitions must be positive")
    if warmups < 0:
        raise EvaluatorError("ablation warmups must not be negative")
    manifest_path = manifest_path.resolve()
    manifest = load_json(manifest_path)
    _validate_manifest(manifest, manifest_path.parent)
    parameter_space_path = parameter_space_path.resolve()
    parameter_space = load_json(parameter_space_path)
    parameter_binding = manifest["parameterSpace"]
    if (
        parameter_binding["parameterSpaceId"] != parameter_space["parameterSpaceId"]
        or parameter_binding["fileName"] != parameter_space_path.name
        or parameter_binding["sha256"] != sha256_file(parameter_space_path)
    ):
        raise EvaluatorError("ablation parameter-space binding mismatch")

    baseline_manifest_path = baseline_manifest_path.resolve()
    tsa_cli_jar = tsa_cli_jar.resolve()
    baseline_manifest = load_json(baseline_manifest_path)
    workload = baseline_manifest.get("workload")
    if not isinstance(workload, dict):
        raise EvaluatorError("baseline manifest has no workload")
    baseline_report_path = (
        baseline_manifest_path.parent
        / baseline_manifest["baselineReport"]["fileName"]
    ).resolve()
    if sha256_file(baseline_report_path) != baseline_manifest["baselineReport"]["sha256"]:
        raise EvaluatorError("ablation baseline report binding mismatch")
    baseline_report = load_json(baseline_report_path)
    validate_baseline_manifest(
        baseline_manifest,
        report_path=baseline_report_path,
        report=baseline_report,
        parameter_space_path=parameter_space_path,
        parameter_space=parameter_space,
        tsa_cli_jar=tsa_cli_jar,
        current_workload=workload,
        expected_timeout_penalty=timeout_penalty,
    )
    profile_paths = [Path(entry["path"]) for entry in workload["profiles"]]

    output_directory = output_directory.resolve()
    reports_directory = output_directory / "reports"
    scores_directory = output_directory / "scores"
    reports_directory.mkdir(parents=True, exist_ok=True)
    scores_directory.mkdir(parents=True, exist_ok=True)
    results_path = output_directory / "results-v0.json"
    results: list[dict[str, Any]] = []
    entries, selection = _select_entries(
        manifest,
        eligibility_results_path=eligibility_results_path,
    )
    for index, entry in enumerate(entries, start=1):
        candidate_path = (manifest_path.parent / entry["path"]).resolve()
        candidate = load_json(candidate_path)
        normalized_candidate = normalize_candidate(candidate, parameter_space)
        report_path = reports_directory / f"subset-{entry['mask']:02d}-v0.json"
        print(
            f"ablation {index}/{len(entries)}: mask={entry['mask']:02d}, "
            f"options={','.join(entry['optionNames']) or '<default>'}"
        )
        if report_path.is_file():
            report = load_json(report_path)
            validate_report_against_workload(
                report,
                parameter_space,
                workload,
                expected_repetitions=repetitions,
                expected_warmups=warmups,
            )
            if report["candidate"] != normalized_candidate:
                raise EvaluatorError(
                    f"persisted ablation report belongs to another candidate: {report_path}"
                )
        else:
            report = run_profile_replay_matrix(
                tsa_cli_jar=tsa_cli_jar,
                candidate=candidate,
                parameter_space=parameter_space,
                output_path=report_path,
                profiles=profile_paths,
                backend_role=workload["backendRole"],
                repetitions=repetitions,
                warmups=warmups,
                matrix_timeout_seconds=matrix_timeout_seconds,
                trial_timeout_seconds=trial_timeout_seconds,
                progress_interval_seconds=progress_interval_seconds,
            )
        score = score_reports(
            baseline_report,
            report,
            timeout_penalty=timeout_penalty,
            aggregation=WORKLOAD_TOTAL_AGGREGATION,
        )
        validate_score(score)
        score_path = scores_directory / f"subset-{entry['mask']:02d}-v0.json"
        write_json(score_path, score)
        geometric_speedup = (
            math.exp(score["reward"]) if score["eligible"] else None
        )
        if entry["mask"] == 0:
            fresh_default_speedup = geometric_speedup
        else:
            default_result = next(
                (item for item in results if item["mask"] == 0), None
            )
            fresh_default_speedup = (
                default_result["geometricSpeedup"]
                if default_result is not None
                else None
            )
        results.append(
            {
                "ordinal": entry["ordinal"],
                "mask": entry["mask"],
                "optionNames": entry["optionNames"],
                "candidateId": entry["candidateId"],
                "eligible": score["eligible"],
                "reward": score["reward"],
                "geometricSpeedup": geometric_speedup,
                "speedupVsFreshDefault": geometric_speedup / fresh_default_speedup
                if geometric_speedup is not None
                and fresh_default_speedup is not None
                else None,
                "wrongAnswerCount": score["totals"]["wrongAnswerCount"],
                "knownStatusTimeoutCount": score["totals"][
                    "knownStatusTimeoutCount"
                ],
                "failedReplayCount": score["totals"]["failedReplayCount"],
                "reportSha256": sha256_file(report_path),
                "scoreSha256": sha256_file(score_path),
            }
        )
        write_json(
            results_path,
            _results_document(
                manifest=manifest,
                baseline_manifest_path=baseline_manifest_path,
                workload=workload,
                repetitions=repetitions,
                warmups=warmups,
                selection=selection,
                results=results,
                complete=len(results) == len(entries),
            ),
        )
    return load_json(results_path)


def _results_document(
    *,
    manifest: Mapping[str, Any],
    baseline_manifest_path: Path,
    workload: Mapping[str, Any],
    repetitions: int,
    warmups: int,
    selection: Mapping[str, Any],
    results: list[dict[str, Any]],
    complete: bool,
) -> dict[str, Any]:
    return {
        "schemaVersion": ABLATION_RESULTS_SCHEMA_VERSION,
        "sourceCandidateId": manifest["sourceCandidate"]["candidateId"],
        "baselineManifest": {
            "fileName": baseline_manifest_path.name,
            "sha256": sha256_file(baseline_manifest_path),
        },
        "workloadSha256": workload["workloadSha256"],
        "repetitions": repetitions,
        "warmups": warmups,
        "selection": dict(selection),
        "complete": complete,
        "results": results,
    }


def _select_entries(
    manifest: Mapping[str, Any],
    *,
    eligibility_results_path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entries = list(manifest["candidates"])
    if eligibility_results_path is None:
        return entries, {"kind": "all-subsets-v0"}
    eligibility_results_path = eligibility_results_path.resolve()
    eligibility = load_json(eligibility_results_path)
    if (
        eligibility.get("schemaVersion") != ABLATION_RESULTS_SCHEMA_VERSION
        or eligibility.get("sourceCandidateId")
        != manifest["sourceCandidate"]["candidateId"]
        or eligibility.get("complete") is not True
    ):
        raise EvaluatorError("eligibility results are incomplete or incompatible")
    decisions = eligibility.get("results")
    if not isinstance(decisions, list):
        raise EvaluatorError("eligibility results have no candidate decisions")
    decision_by_mask: dict[int, bool] = {}
    for decision in decisions:
        mask = decision.get("mask") if isinstance(decision, dict) else None
        eligible = decision.get("eligible") if isinstance(decision, dict) else None
        if (
            not isinstance(mask, int)
            or isinstance(mask, bool)
            or not isinstance(eligible, bool)
            or mask in decision_by_mask
        ):
            raise EvaluatorError("eligibility decision is malformed or duplicated")
        decision_by_mask[mask] = eligible
    expected_masks = {entry["mask"] for entry in entries}
    if set(decision_by_mask) != expected_masks:
        raise EvaluatorError("eligibility results do not cover every ablation subset")
    selected = [entry for entry in entries if decision_by_mask[entry["mask"]]]
    if not selected:
        raise EvaluatorError("eligibility screen rejected every ablation subset")
    if selected[0]["mask"] != 0:
        raise EvaluatorError("eligibility screen rejected the fresh default subset")
    return selected, {
        "kind": "eligible-from-results-v0",
        "sourceFileName": eligibility_results_path.name,
        "sourceSha256": sha256_file(eligibility_results_path),
        "selectedMasks": [entry["mask"] for entry in selected],
    }


def _validate_manifest(manifest: Mapping[str, Any], base_directory: Path) -> None:
    if manifest.get("schemaVersion") != ABLATION_MANIFEST_SCHEMA_VERSION:
        raise EvaluatorError("unsupported ablation manifest schema")
    policy = manifest.get("policy")
    entries = manifest.get("candidates")
    if not isinstance(policy, dict) or not isinstance(entries, list) or not entries:
        raise EvaluatorError("ablation manifest is malformed")
    option_names = policy.get("optionNames")
    if not isinstance(option_names, list) or not option_names:
        raise EvaluatorError("ablation manifest has no option names")
    if policy.get("candidateCount") != 1 << len(option_names):
        raise EvaluatorError("ablation candidate count is not exhaustive")
    if len(entries) != policy["candidateCount"]:
        raise EvaluatorError("ablation candidate list is incomplete")
    masks: set[int] = set()
    candidate_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise EvaluatorError("ablation candidate entry must be an object")
        mask = entry.get("mask")
        if not isinstance(mask, int) or isinstance(mask, bool) or mask in masks:
            raise EvaluatorError("ablation candidate mask is invalid or duplicated")
        masks.add(mask)
        expected_names = [
            name for index, name in enumerate(option_names) if mask & (1 << index)
        ]
        if entry.get("optionNames") != expected_names:
            raise EvaluatorError("ablation candidate option names do not match mask")
        candidate_id = entry.get("candidateId")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in candidate_ids:
            raise EvaluatorError("ablation candidate ID is invalid or duplicated")
        candidate_ids.add(candidate_id)
        candidate_path = (base_directory / entry.get("path", "")).resolve()
        if not candidate_path.is_file() or sha256_file(candidate_path) != entry.get(
            "sha256"
        ):
            raise EvaluatorError(f"ablation candidate binding mismatch: {candidate_path}")
    if masks != set(range(1 << len(option_names))):
        raise EvaluatorError("ablation manifest does not contain every subset")


def _write_or_verify(path: Path, value: Mapping[str, Any], label: str) -> None:
    if path.is_file():
        if load_json(path) != value:
            raise EvaluatorError(f"existing {label} differs: {path}")
        return
    write_json(path, value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-candidate", type=Path, required=True)
    prepare.add_argument("--parameter-space", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)

    run = commands.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--baseline-manifest", type=Path, required=True)
    run.add_argument("--parameter-space", type=Path, required=True)
    run.add_argument("--tsa-cli-jar", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--repetitions", type=int, default=1)
    run.add_argument("--warmups", type=int, default=0)
    run.add_argument("--matrix-timeout-seconds", type=float)
    run.add_argument("--trial-timeout-seconds", type=float)
    run.add_argument("--progress-interval-seconds", type=int, default=15)
    run.add_argument("--timeout-penalty", type=float, default=DEFAULT_TIMEOUT_PENALTY)
    run.add_argument("--eligibility-results", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        if arguments.command == "prepare":
            manifest = prepare_option_subset_ablation(
                source_candidate_path=arguments.source_candidate,
                parameter_space_path=arguments.parameter_space,
                output_directory=arguments.output_dir,
            )
            print(
                f"prepared {len(manifest['candidates'])} option subsets in "
                f"{arguments.output_dir.resolve()}"
            )
        else:
            results = run_option_subset_ablation(
                manifest_path=arguments.manifest,
                baseline_manifest_path=arguments.baseline_manifest,
                parameter_space_path=arguments.parameter_space,
                tsa_cli_jar=arguments.tsa_cli_jar,
                output_directory=arguments.output_dir,
                repetitions=arguments.repetitions,
                warmups=arguments.warmups,
                matrix_timeout_seconds=arguments.matrix_timeout_seconds,
                trial_timeout_seconds=arguments.trial_timeout_seconds,
                progress_interval_seconds=arguments.progress_interval_seconds,
                timeout_penalty=arguments.timeout_penalty,
                eligibility_results_path=arguments.eligibility_results,
            )
            eligible = sum(item["eligible"] for item in results["results"])
            print(
                f"ablation complete={results['complete']}; "
                f"eligible={eligible}/{len(results['results'])}; "
                f"report={arguments.output_dir.resolve() / 'results-v0.json'}"
            )
    except (EvaluatorError, OSError, ValueError) as failure:
        print(f"option ablation failed: {failure}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
