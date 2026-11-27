"""Resume-safe execution of a paired randomized confirmation schedule."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from ksmt_ml.evaluator import (
    DEFAULT_TIMEOUT_PENALTY,
    PROFILE_MACRO_AGGREGATION,
    SUPPORTED_SCORE_AGGREGATIONS,
    load_json,
    normalize_candidate,
    run_profile_replay_matrix,
    score_reports,
    sha256_file,
    validate_report_against_workload,
    write_json,
)
from ksmt_ml.experiment_design import (
    OBSERVATIONS_SCHEMA_VERSION,
    ExperimentDesignError,
    load_workload_corpus,
    validate_confirmation_schedule,
    validate_workload_corpus,
)


CANDIDATE_INDEX_SCHEMA_VERSION = "cvc5-confirmation-candidate-index-v0"


def _relative_binding(path: Path, base_directory: Path) -> dict[str, str]:
    return {
        "path": os.path.relpath(path.resolve(), base_directory.resolve()),
        "sha256": sha256_file(path.resolve()),
    }


def _candidate_for_trial(
    *,
    trial: Mapping[str, Any],
    baseline_manifest: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
    default_controls: set[str],
    parameter_space: Mapping[str, Any],
) -> dict[str, Any]:
    default = baseline_manifest.get("defaultCandidate")
    if not isinstance(default, dict):
        raise ExperimentDesignError("baseline manifest has no default candidate")
    if trial["configurationRole"] == "default":
        normalize_candidate(default, parameter_space)
        return dict(default)
    candidate_id = trial["candidateId"]
    if candidate_id in default_controls:
        control = dict(default)
        control["candidateId"] = candidate_id
        normalize_candidate(control, parameter_space)
        return control
    candidate = candidates.get(candidate_id)
    if candidate is None:
        raise ExperimentDesignError(f"no candidate artifact for {candidate_id}")
    normalize_candidate(candidate, parameter_space)
    return dict(candidate)


def _run_or_reuse_trial(
    *,
    trial: Mapping[str, Any],
    output_path: Path,
    baseline_manifest: Mapping[str, Any],
    candidate: Mapping[str, Any],
    parameter_space: Mapping[str, Any],
    tsa_cli_jar: Path,
    java: str,
    warmups: int,
    matrix_timeout_seconds: float | None,
    trial_timeout_seconds: float | None,
    progress_interval_seconds: int,
    stream_child_output: bool,
) -> dict[str, Any]:
    workload = baseline_manifest.get("workload")
    if not isinstance(workload, dict):
        raise ExperimentDesignError("baseline manifest has no workload")
    profile_paths = [Path(entry["path"]) for entry in workload["profiles"]]
    normalized_candidate = normalize_candidate(candidate, parameter_space)
    if output_path.is_file():
        report = load_json(output_path)
        validate_report_against_workload(
            report,
            parameter_space,
            workload,
            expected_repetitions=1,
            expected_warmups=warmups,
        )
        if report["candidate"] != normalized_candidate:
            raise ExperimentDesignError(
                f"persisted trial belongs to another candidate: {trial['trialId']}"
            )
        return report
    return run_profile_replay_matrix(
        tsa_cli_jar=tsa_cli_jar,
        candidate=candidate,
        parameter_space=parameter_space,
        output_path=output_path,
        profiles=profile_paths,
        backend_role=workload["backendRole"],
        repetitions=1,
        warmups=warmups,
        java=java,
        matrix_timeout_seconds=matrix_timeout_seconds,
        trial_timeout_seconds=trial_timeout_seconds,
        stream_child_output=stream_child_output,
        progress_interval_seconds=progress_interval_seconds,
    )


def run_confirmation_schedule(
    *,
    schedule_path: Path,
    corpus_path: Path,
    parameter_space_path: Path,
    tsa_cli_jar: Path,
    candidate_paths: Mapping[str, Path],
    output_directory: Path,
    java: str = "java",
    warmups: int = 0,
    matrix_timeout_seconds: float | None = None,
    trial_timeout_seconds: float | None = None,
    progress_interval_seconds: int = 15,
    timeout_penalty: float = DEFAULT_TIMEOUT_PENALTY,
    reward_clip: float = 5.0,
    stream_child_output: bool = True,
    expected_partition: str = "test",
    score_aggregation: str = PROFILE_MACRO_AGGREGATION,
) -> dict[str, Any]:
    """Execute trials in frozen order and persist pair observations after each pair."""

    schedule = load_json(schedule_path)
    validate_confirmation_schedule(schedule)
    corpus = load_workload_corpus(corpus_path)
    baseline_paths = validate_workload_corpus(
        corpus, base_directory=corpus_path.resolve().parent
    )
    parameter_space = load_json(parameter_space_path)
    candidates = {
        candidate_id: load_json(path) for candidate_id, path in candidate_paths.items()
    }
    declared_candidates = {
        item["candidateId"] for item in schedule["candidates"]
    }
    default_controls = {
        item["candidateId"]
        for item in schedule["candidates"]
        if item["defaultControl"]
    }
    missing = declared_candidates - default_controls - set(candidates)
    extra = set(candidates) - declared_candidates
    if missing or extra:
        raise ExperimentDesignError(
            f"candidate artifacts do not match schedule: missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    if expected_partition not in {"validation", "test"}:
        raise ExperimentDesignError("expected partition must be validation or test")
    expected_groups = {
        group["groupId"]
        for group in corpus["groups"]
        if group["partition"] == expected_partition
    }
    if set(schedule["workloadGroupIds"]) != expected_groups:
        raise ExperimentDesignError(
            f"confirmation schedule must contain exactly the {expected_partition} groups"
        )
    if warmups < 0:
        raise ExperimentDesignError("warmups must not be negative")
    if timeout_penalty < 1:
        raise ExperimentDesignError("timeout penalty must be at least one")
    if reward_clip <= 0:
        raise ExperimentDesignError("reward clip must be positive")
    if score_aggregation not in SUPPORTED_SCORE_AGGREGATIONS:
        raise ExperimentDesignError("unsupported confirmation score aggregation")

    output_directory = output_directory.resolve()
    trials_directory = output_directory / "trials"
    observations_path = output_directory / "observations-v0.json"
    trials_directory.mkdir(parents=True, exist_ok=True)
    reports_by_pair: dict[str, dict[str, tuple[Path, Mapping[str, Any]]]] = {}
    observation_results: list[dict[str, Any]] = []

    for trial in schedule["trials"]:
        group_id = trial["workloadGroupId"]
        baseline_manifest = load_json(baseline_paths[group_id])
        parameter_binding = baseline_manifest.get("parameterSpace", {})
        if parameter_binding.get("parameterSpaceId") != parameter_space.get(
            "parameterSpaceId"
        ):
            raise ExperimentDesignError(
                f"workload group {group_id} uses another parameter space"
            )
        if (
            parameter_binding.get("fileName") != parameter_space_path.resolve().name
            or parameter_binding.get("sha256") != sha256_file(parameter_space_path)
        ):
            raise ExperimentDesignError(
                f"workload group {group_id} parameter-space binding mismatch"
            )
        tsa_binding = baseline_manifest.get("tsaCliJar", {})
        if (
            tsa_binding.get("fileName") != tsa_cli_jar.resolve().name
            or tsa_binding.get("sha256") != sha256_file(tsa_cli_jar)
        ):
            raise ExperimentDesignError(
                f"workload group {group_id} TSA CLI binding mismatch"
            )
        expected_timeout_penalty = baseline_manifest.get("evaluationPolicy", {}).get(
            "timeoutPenalty"
        )
        if expected_timeout_penalty != timeout_penalty:
            raise ExperimentDesignError(
                f"workload group {group_id} uses another timeout penalty"
            )
        candidate = _candidate_for_trial(
            trial=trial,
            baseline_manifest=baseline_manifest,
            candidates=candidates,
            default_controls=default_controls,
            parameter_space=parameter_space,
        )
        output_path = trials_directory / (
            f"{trial['ordinal']:06d}-{trial['trialId']}.json"
        )
        print(
            f"confirmation {trial['ordinal'] + 1}/{len(schedule['trials'])}: "
            f"group={group_id}, pair={trial['pairId']}, "
            f"role={trial['configurationRole']}"
        )
        report = _run_or_reuse_trial(
            trial=trial,
            output_path=output_path,
            baseline_manifest=baseline_manifest,
            candidate=candidate,
            parameter_space=parameter_space,
            tsa_cli_jar=tsa_cli_jar,
            java=java,
            warmups=warmups,
            matrix_timeout_seconds=matrix_timeout_seconds,
            trial_timeout_seconds=trial_timeout_seconds,
            progress_interval_seconds=progress_interval_seconds,
            stream_child_output=stream_child_output,
        )
        pair_reports = reports_by_pair.setdefault(trial["pairId"], {})
        pair_reports[trial["configurationRole"]] = (output_path, report)
        if set(pair_reports) == {"default", "candidate"}:
            default_path, default_report = pair_reports["default"]
            candidate_path, candidate_report = pair_reports["candidate"]
            score = score_reports(
                default_report,
                candidate_report,
                timeout_penalty=timeout_penalty,
                reward_clip=reward_clip,
                aggregation=score_aggregation,
            )
            observation_results.append(
                {
                    "pairId": trial["pairId"],
                    "candidateId": trial["candidateId"],
                    "workloadGroupId": group_id,
                    "repetition": trial["repetition"],
                    "defaultReport": _relative_binding(
                        default_path, observations_path.parent
                    ),
                    "candidateReport": _relative_binding(
                        candidate_path, observations_path.parent
                    ),
                    "reward": score["reward"],
                    "eligible": score["eligible"],
                    "wrongAnswerCount": score["totals"]["wrongAnswerCount"],
                    "timeoutCount": score["totals"]["knownStatusTimeoutCount"],
                    "failureCount": score["totals"]["failedReplayCount"],
                }
            )
            write_json(
                observations_path,
                {
                    "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
                    "scheduleSha256": schedule["scheduleSha256"],
                    "scoreAggregation": score_aggregation,
                    "complete": len(observation_results)
                    == len(schedule["trials"]) // 2,
                    "results": observation_results,
                },
            )
    return load_json(observations_path)


def _candidate_binding(value: str) -> tuple[str, Path]:
    candidate_id, separator, path = value.partition("=")
    if not separator or not candidate_id or not path:
        raise argparse.ArgumentTypeError("candidate must be CANDIDATE_ID=PATH")
    return candidate_id, Path(path)


def load_candidate_index(
    path: Path, *, expected_schedule_sha256: str
) -> dict[str, Path]:
    index = load_json(path)
    if set(index) != {"schemaVersion", "scheduleSha256", "candidates"} or (
        index.get("schemaVersion") != CANDIDATE_INDEX_SCHEMA_VERSION
    ):
        raise ExperimentDesignError("confirmation candidate index fields do not match v0")
    if index.get("scheduleSha256") != expected_schedule_sha256:
        raise ExperimentDesignError("candidate index belongs to another schedule")
    candidates = index.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ExperimentDesignError("candidate index is empty")
    result: dict[str, Path] = {}
    for candidate in candidates:
        expected_fields = {
            "finalistId",
            "algorithm",
            "seed",
            "candidateId",
            "path",
            "sha256",
        }
        if not isinstance(candidate, dict) or set(candidate) != expected_fields:
            raise ExperimentDesignError("candidate index entry is malformed")
        finalist_id = candidate.get("finalistId")
        if not isinstance(finalist_id, str) or not finalist_id or finalist_id in result:
            raise ExperimentDesignError("candidate index finalist IDs must be unique")
        candidate_path = Path(candidate.get("path", ""))
        if not candidate_path.is_absolute():
            candidate_path = path.resolve().parent / candidate_path
        candidate_path = candidate_path.resolve()
        if not candidate_path.is_file() or sha256_file(candidate_path) != candidate.get(
            "sha256"
        ):
            raise ExperimentDesignError(
                f"candidate index binding mismatch: {candidate_path}"
            )
        candidate_document = load_json(candidate_path)
        if candidate_document.get("candidateId") != candidate.get("candidateId"):
            raise ExperimentDesignError("candidate index candidateId mismatch")
        result[finalist_id] = candidate_path
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--parameter-space", type=Path, required=True)
    parser.add_argument("--tsa-cli-jar", type=Path, required=True)
    parser.add_argument("--candidate", type=_candidate_binding, action="append", default=[])
    parser.add_argument("--candidate-index", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--warmups", type=int, default=0)
    parser.add_argument("--matrix-timeout-seconds", type=float)
    parser.add_argument("--trial-timeout-seconds", type=float)
    parser.add_argument("--progress-interval-seconds", type=int, default=15)
    parser.add_argument("--timeout-penalty", type=float, default=DEFAULT_TIMEOUT_PENALTY)
    parser.add_argument("--reward-clip", type=float, default=5.0)
    parser.add_argument(
        "--score-aggregation",
        choices=sorted(SUPPORTED_SCORE_AGGREGATIONS),
        default=PROFILE_MACRO_AGGREGATION,
    )
    parser.add_argument("--quiet-child-output", action="store_true")
    parser.add_argument(
        "--expected-partition", choices=("validation", "test"), default="test"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        candidate_paths = dict(arguments.candidate)
        if len(candidate_paths) != len(arguments.candidate):
            raise ExperimentDesignError("candidate IDs must be unique")
        if arguments.candidate_index is not None:
            if candidate_paths:
                raise ExperimentDesignError(
                    "use either --candidate-index or repeated --candidate, not both"
                )
            schedule = load_json(arguments.schedule)
            validate_confirmation_schedule(schedule)
            candidate_paths = load_candidate_index(
                arguments.candidate_index,
                expected_schedule_sha256=schedule["scheduleSha256"],
            )
        observations = run_confirmation_schedule(
            schedule_path=arguments.schedule,
            corpus_path=arguments.corpus,
            parameter_space_path=arguments.parameter_space,
            tsa_cli_jar=arguments.tsa_cli_jar,
            candidate_paths=candidate_paths,
            output_directory=arguments.output_dir,
            java=arguments.java,
            warmups=arguments.warmups,
            matrix_timeout_seconds=arguments.matrix_timeout_seconds,
            trial_timeout_seconds=arguments.trial_timeout_seconds,
            progress_interval_seconds=arguments.progress_interval_seconds,
            timeout_penalty=arguments.timeout_penalty,
            reward_clip=arguments.reward_clip,
            stream_child_output=not arguments.quiet_child_output,
            expected_partition=arguments.expected_partition,
            score_aggregation=arguments.score_aggregation,
        )
        print(
            f"confirmation complete={observations['complete']}; "
            f"pairs={len(observations['results'])}"
        )
        return 0
    except (OSError, ValueError) as failure:
        print(f"confirmation runner failed: {failure}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
