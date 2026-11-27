"""Frozen multi-workload experiment design and paired confirmation helpers.

This module deliberately does not execute TSA or cvc5.  It freezes the
independent workload groups, configurator seeds, shared budgets, confirmation
order, and grouped statistics before a costly claim-bearing campaign starts.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from ksmt_ml.evaluator import (
    PROFILE_MACRO_AGGREGATION,
    SUPPORTED_SCORE_AGGREGATIONS,
    load_json,
    score_reports,
    sha256_file,
    sha256_json,
    validate_workload_manifest,
    write_json,
)


CORPUS_SCHEMA_VERSION = "cvc5-workload-corpus-v0"
CAMPAIGN_SCHEMA_VERSION = "cvc5-configurator-campaign-v0"
SCHEDULE_SCHEMA_VERSION = "cvc5-confirmation-schedule-v0"
OBSERVATIONS_SCHEMA_VERSION = "cvc5-confirmation-observations-v0"
SUMMARY_SCHEMA_VERSION = "cvc5-grouped-confirmation-summary-v0"
NOISE_SUMMARY_SCHEMA_VERSION = "cvc5-default-noise-calibration-summary-v0"
CONFIRMATION_POLICY = "paired-randomized-interleaved-v0"
BALANCED_CONFIRMATION_POLICY = "paired-balanced-randomized-interleaved-v0"
PARTITIONS = frozenset({"development", "validation", "test"})
ALGORITHMS = frozenset({"random", "evolution", "smac"})


class ExperimentDesignError(ValueError):
    """A frozen corpus, campaign, schedule, or observation set is invalid."""


def _exact_fields(value: Mapping[str, Any], expected: set[str], subject: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ExperimentDesignError(
            f"{subject} fields do not match v0: missing={missing}, extra={extra}"
        )


def _string(value: Mapping[str, Any], field: str, subject: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ExperimentDesignError(f"{subject} has invalid {field}")
    return result


def _non_negative_int(value: Any, subject: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExperimentDesignError(f"{subject} must be a non-negative integer")
    return value


def _positive_number(value: Any, subject: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentDesignError(f"{subject} must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ExperimentDesignError(f"{subject} must be a positive number")
    return result


def _resolve_binding(base_directory: Path, binding: Mapping[str, Any], subject: str) -> Path:
    _exact_fields(binding, {"path", "sha256"}, subject)
    path_text = _string(binding, "path", subject)
    expected_sha256 = binding.get("sha256")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ExperimentDesignError(f"{subject} has invalid sha256")
    path = Path(path_text)
    if not path.is_absolute():
        path = base_directory / path
    path = path.resolve()
    if not path.is_file():
        raise ExperimentDesignError(f"{subject} does not exist: {path}")
    if sha256_file(path) != expected_sha256:
        raise ExperimentDesignError(f"{subject} sha256 mismatch: {path}")
    return path


def validate_workload_corpus(
    corpus: Mapping[str, Any], *, base_directory: Path
) -> dict[str, Path]:
    """Validate grouped split integrity and bind every group to its baseline."""

    _exact_fields(
        corpus,
        {
            "schemaVersion",
            "corpusId",
            "targetSolver",
            "solverVersion",
            "parameterSpaceId",
            "groupingKey",
            "groups",
        },
        "workload corpus",
    )
    if corpus.get("schemaVersion") != CORPUS_SCHEMA_VERSION:
        raise ExperimentDesignError("unsupported workload corpus schema")
    if corpus.get("targetSolver") != "cvc5":
        raise ExperimentDesignError("workload corpus target solver must be cvc5")
    _string(corpus, "corpusId", "workload corpus")
    solver_version = _string(corpus, "solverVersion", "workload corpus")
    parameter_space_id = _string(corpus, "parameterSpaceId", "workload corpus")
    _string(corpus, "groupingKey", "workload corpus")
    groups = corpus.get("groups")
    if not isinstance(groups, list) or len(groups) < 3:
        raise ExperimentDesignError("workload corpus requires at least three groups")

    group_ids: set[str] = set()
    leakage_partitions: dict[str, str] = {}
    workload_digests: set[str] = set()
    partition_counts = {partition: 0 for partition in PARTITIONS}
    resolved: dict[str, Path] = {}
    for index, item in enumerate(groups):
        subject = f"workload group {index}"
        if not isinstance(item, dict):
            raise ExperimentDesignError(f"{subject} must be an object")
        _exact_fields(
            item,
            {
                "groupId",
                "leakageGroupId",
                "partition",
                "sealedUntilFinalEvaluation",
                "baselineManifest",
            },
            subject,
        )
        group_id = _string(item, "groupId", subject)
        leakage_group_id = _string(item, "leakageGroupId", subject)
        partition = item.get("partition")
        if partition not in PARTITIONS:
            raise ExperimentDesignError(f"{subject} has invalid partition")
        sealed = item.get("sealedUntilFinalEvaluation")
        if not isinstance(sealed, bool) or sealed != (partition == "test"):
            raise ExperimentDesignError(
                f"{subject} must be sealed exactly when partition is test"
            )
        if group_id in group_ids:
            raise ExperimentDesignError(f"duplicate workload groupId: {group_id}")
        group_ids.add(group_id)
        previous_partition = leakage_partitions.setdefault(leakage_group_id, partition)
        if previous_partition != partition:
            raise ExperimentDesignError(
                f"leakage group {leakage_group_id!r} crosses partitions"
            )
        partition_counts[partition] += 1

        binding = item.get("baselineManifest")
        if not isinstance(binding, dict):
            raise ExperimentDesignError(f"{subject} baselineManifest must be an object")
        baseline_path = _resolve_binding(
            base_directory, binding, f"{subject} baseline manifest"
        )
        baseline = load_json(baseline_path)
        if baseline.get("schemaVersion") != "cvc5-baseline-manifest-v0":
            raise ExperimentDesignError(f"{subject} has unsupported baseline schema")
        parameter_space = baseline.get("parameterSpace")
        if not isinstance(parameter_space, dict) or (
            parameter_space.get("parameterSpaceId") != parameter_space_id
        ):
            raise ExperimentDesignError(
                f"{subject} baseline uses another parameter space"
            )
        workload = baseline.get("workload")
        if not isinstance(workload, dict):
            raise ExperimentDesignError(f"{subject} baseline has no workload")
        validate_workload_manifest(workload)
        workload_digest = workload["workloadSha256"]
        if workload_digest in workload_digests:
            raise ExperimentDesignError(
                f"the same exact workload is bound to more than one group: {group_id}"
            )
        workload_digests.add(workload_digest)
        actual_versions = {profile["solverVersion"] for profile in workload["profiles"]}
        if actual_versions != {solver_version}:
            raise ExperimentDesignError(
                f"{subject} solver versions differ from corpus solverVersion"
            )
        resolved[group_id] = baseline_path

    missing_partitions = sorted(
        partition for partition, count in partition_counts.items() if count == 0
    )
    if missing_partitions:
        raise ExperimentDesignError(
            f"workload corpus has empty partitions: {missing_partitions}"
        )
    return resolved


def load_workload_corpus(path: Path) -> dict[str, Any]:
    corpus = load_json(path)
    validate_workload_corpus(corpus, base_directory=path.resolve().parent)
    return corpus


def build_workload_corpus(
    *,
    corpus_id: str,
    solver_version: str,
    parameter_space_id: str,
    grouping_key: str,
    groups: Sequence[tuple[str, str, str, Path]],
    output_path: Path,
) -> dict[str, Any]:
    """Bind real baseline manifests into a portable grouped corpus artifact."""

    if not corpus_id or not solver_version or not parameter_space_id or not grouping_key:
        raise ExperimentDesignError("corpus identifiers must be non-empty")
    output_directory = output_path.resolve().parent
    entries = []
    for partition, group_id, leakage_group_id, baseline_path in groups:
        resolved_baseline = baseline_path.resolve()
        if not resolved_baseline.is_file():
            raise ExperimentDesignError(
                f"baseline manifest does not exist: {resolved_baseline}"
            )
        relative_path = os.path.relpath(resolved_baseline, output_directory)
        entries.append(
            {
                "groupId": group_id,
                "leakageGroupId": leakage_group_id,
                "partition": partition,
                "sealedUntilFinalEvaluation": partition == "test",
                "baselineManifest": {
                    "path": relative_path,
                    "sha256": sha256_file(resolved_baseline),
                },
            }
        )
    corpus = {
        "schemaVersion": CORPUS_SCHEMA_VERSION,
        "corpusId": corpus_id,
        "targetSolver": "cvc5",
        "solverVersion": solver_version,
        "parameterSpaceId": parameter_space_id,
        "groupingKey": grouping_key,
        "groups": entries,
    }
    validate_workload_corpus(corpus, base_directory=output_directory)
    write_json(output_path, corpus)
    return corpus


def validate_configurator_campaign(
    campaign: Mapping[str, Any], *, base_directory: Path
) -> dict[str, Any]:
    """Validate equal-budget, equal-seed configurator comparison inputs."""

    _exact_fields(
        campaign,
        {
            "schemaVersion",
            "campaignId",
            "corpus",
            "defaultCandidateId",
            "objective",
            "algorithms",
            "budget",
            "confirmation",
            "gate",
        },
        "configurator campaign",
    )
    if campaign.get("schemaVersion") != CAMPAIGN_SCHEMA_VERSION:
        raise ExperimentDesignError("unsupported configurator campaign schema")
    _string(campaign, "campaignId", "configurator campaign")
    _string(campaign, "defaultCandidateId", "configurator campaign")
    if campaign.get("objective") not in SUPPORTED_SCORE_AGGREGATIONS:
        raise ExperimentDesignError("unsupported campaign objective")

    corpus_binding = campaign.get("corpus")
    if not isinstance(corpus_binding, dict):
        raise ExperimentDesignError("campaign corpus binding must be an object")
    _exact_fields(corpus_binding, {"path", "sha256", "corpusId"}, "corpus binding")
    corpus_id = _string(corpus_binding, "corpusId", "corpus binding")
    corpus_file_binding = {
        "path": corpus_binding.get("path"),
        "sha256": corpus_binding.get("sha256"),
    }
    corpus_path = _resolve_binding(base_directory, corpus_file_binding, "corpus binding")
    corpus = load_json(corpus_path)
    validate_workload_corpus(corpus, base_directory=corpus_path.parent)
    if corpus.get("corpusId") != corpus_id:
        raise ExperimentDesignError("campaign corpusId does not match corpus artifact")

    algorithms = campaign.get("algorithms")
    if not isinstance(algorithms, list) or len(algorithms) != len(ALGORITHMS):
        raise ExperimentDesignError("campaign must declare random, evolution, and smac")
    seen_algorithms: set[str] = set()
    common_seeds: tuple[int, ...] | None = None
    for item in algorithms:
        if not isinstance(item, dict):
            raise ExperimentDesignError("campaign algorithm must be an object")
        _exact_fields(item, {"name", "seeds"}, "campaign algorithm")
        name = item.get("name")
        if name not in ALGORITHMS or name in seen_algorithms:
            raise ExperimentDesignError("campaign algorithm names must be unique")
        seen_algorithms.add(name)
        seeds = item.get("seeds")
        if not isinstance(seeds, list) or len(seeds) < 2:
            raise ExperimentDesignError("each algorithm requires at least two seeds")
        normalized_seeds = tuple(_non_negative_int(seed, "algorithm seed") for seed in seeds)
        if len(set(normalized_seeds)) != len(normalized_seeds):
            raise ExperimentDesignError("algorithm seeds must be unique")
        if common_seeds is None:
            common_seeds = normalized_seeds
        elif normalized_seeds != common_seeds:
            raise ExperimentDesignError(
                "all algorithms must use the same ordered seed set"
            )
    if seen_algorithms != ALGORITHMS:
        raise ExperimentDesignError("campaign must declare random, evolution, and smac")

    budget = campaign.get("budget")
    if not isinstance(budget, dict):
        raise ExperimentDesignError("campaign budget must be an object")
    _exact_fields(
        budget,
        {"wallClockSeconds", "maximumEvaluations", "perTrialTimeoutSeconds"},
        "campaign budget",
    )
    _positive_number(budget.get("wallClockSeconds"), "wallClockSeconds")
    maximum_evaluations = budget.get("maximumEvaluations")
    if (
        isinstance(maximum_evaluations, bool)
        or not isinstance(maximum_evaluations, int)
        or maximum_evaluations < 1
    ):
        raise ExperimentDesignError("maximumEvaluations must be a positive integer")
    _positive_number(budget.get("perTrialTimeoutSeconds"), "perTrialTimeoutSeconds")

    confirmation = campaign.get("confirmation")
    if not isinstance(confirmation, dict):
        raise ExperimentDesignError("campaign confirmation must be an object")
    _exact_fields(
        confirmation,
        {"repetitions", "scheduleSeed", "orderPolicy"},
        "campaign confirmation",
    )
    repetitions = confirmation.get("repetitions")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 2:
        raise ExperimentDesignError("confirmation repetitions must be at least two")
    _non_negative_int(confirmation.get("scheduleSeed"), "confirmation scheduleSeed")
    if confirmation.get("orderPolicy") != CONFIRMATION_POLICY:
        raise ExperimentDesignError("unsupported confirmation order policy")

    gate = campaign.get("gate")
    if not isinstance(gate, dict):
        raise ExperimentDesignError("campaign gate must be an object")
    _exact_fields(
        gate,
        {
            "minimumRelativeImprovement",
            "minimumPositiveGroupFraction",
            "requirePositiveBootstrap95LowerBound",
            "requireFailureFree",
        },
        "campaign gate",
    )
    minimum_improvement = gate.get("minimumRelativeImprovement")
    if (
        isinstance(minimum_improvement, bool)
        or not isinstance(minimum_improvement, (int, float))
        or not 0 < float(minimum_improvement) < 1
    ):
        raise ExperimentDesignError(
            "campaign gate has invalid minimumRelativeImprovement"
        )
    positive_fraction = gate.get("minimumPositiveGroupFraction")
    if (
        isinstance(positive_fraction, bool)
        or not isinstance(positive_fraction, (int, float))
        or not 0 < float(positive_fraction) <= 1
    ):
        raise ExperimentDesignError(
            "campaign gate has invalid minimumPositiveGroupFraction"
        )
    if gate.get("requirePositiveBootstrap95LowerBound") is not True:
        raise ExperimentDesignError("campaign must require a positive interval")
    if gate.get("requireFailureFree") is not True:
        raise ExperimentDesignError("campaign must require failure-free confirmation")
    return corpus


def load_configurator_campaign(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    campaign = load_json(path)
    corpus = validate_configurator_campaign(
        campaign, base_directory=path.resolve().parent
    )
    return campaign, corpus


def build_configurator_campaign(
    *,
    campaign_id: str,
    corpus_path: Path,
    default_candidate_id: str,
    seeds: Sequence[int],
    wall_clock_seconds: float,
    maximum_evaluations: int,
    per_trial_timeout_seconds: float,
    confirmation_repetitions: int,
    confirmation_seed: int,
    minimum_relative_improvement: float,
    minimum_positive_group_fraction: float,
    output_path: Path,
    objective: str = PROFILE_MACRO_AGGREGATION,
) -> dict[str, Any]:
    """Build the frozen equal-budget RS/ES/SMAC campaign manifest."""

    corpus = load_workload_corpus(corpus_path)
    normalized_seeds = list(seeds)
    campaign = {
        "schemaVersion": CAMPAIGN_SCHEMA_VERSION,
        "campaignId": campaign_id,
        "corpus": {
            "path": os.path.relpath(
                corpus_path.resolve(), output_path.resolve().parent
            ),
            "sha256": sha256_file(corpus_path.resolve()),
            "corpusId": corpus["corpusId"],
        },
        "defaultCandidateId": default_candidate_id,
        "objective": objective,
        "algorithms": [
            {"name": name, "seeds": normalized_seeds}
            for name in sorted(ALGORITHMS)
        ],
        "budget": {
            "wallClockSeconds": wall_clock_seconds,
            "maximumEvaluations": maximum_evaluations,
            "perTrialTimeoutSeconds": per_trial_timeout_seconds,
        },
        "confirmation": {
            "repetitions": confirmation_repetitions,
            "scheduleSeed": confirmation_seed,
            "orderPolicy": CONFIRMATION_POLICY,
        },
        "gate": {
            "minimumRelativeImprovement": minimum_relative_improvement,
            "minimumPositiveGroupFraction": minimum_positive_group_fraction,
            "requirePositiveBootstrap95LowerBound": True,
            "requireFailureFree": True,
        },
    }
    validate_configurator_campaign(campaign, base_directory=output_path.resolve().parent)
    write_json(output_path, campaign)
    return campaign


def build_confirmation_schedule(
    *,
    campaign_id: str,
    default_candidate_id: str,
    workload_group_ids: Sequence[str],
    candidate_ids: Sequence[str],
    repetitions: int,
    seed: int,
    default_control_ids: Sequence[str] = (),
    balanced_role_order: bool = False,
) -> dict[str, Any]:
    """Create adjacent randomized default/candidate pairs for drift control."""

    if not campaign_id or not default_candidate_id:
        raise ExperimentDesignError("campaign and default candidate IDs are required")
    groups = list(workload_group_ids)
    candidates = list(candidate_ids)
    if not groups or len(set(groups)) != len(groups) or any(not item for item in groups):
        raise ExperimentDesignError("workload group IDs must be non-empty and unique")
    if not candidates or len(set(candidates)) != len(candidates) or any(
        not item for item in candidates
    ):
        raise ExperimentDesignError("candidate IDs must be non-empty and unique")
    if default_candidate_id in candidates:
        raise ExperimentDesignError("default candidate cannot also be a finalist ID")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 2:
        raise ExperimentDesignError("confirmation repetitions must be at least two")
    if balanced_role_order and repetitions % 2:
        raise ExperimentDesignError(
            "balanced confirmation requires an even repetition count"
        )
    _non_negative_int(seed, "confirmation seed")
    default_controls = set(default_control_ids)
    unknown_controls = default_controls - set(candidates)
    if unknown_controls:
        raise ExperimentDesignError(
            f"unknown default-control candidates: {sorted(unknown_controls)}"
        )

    pairs = [
        (group_id, candidate_id, repetition)
        for group_id in groups
        for candidate_id in candidates
        for repetition in range(repetitions)
    ]
    generator = random.Random(seed)
    generator.shuffle(pairs)
    candidate_first_by_coordinate: dict[tuple[str, str, int], bool] = {}
    if balanced_role_order:
        for group_id in groups:
            for candidate_id in candidates:
                candidate_first = [False] * (repetitions // 2) + [True] * (
                    repetitions // 2
                )
                generator.shuffle(candidate_first)
                for repetition, value in enumerate(candidate_first):
                    candidate_first_by_coordinate[
                        (group_id, candidate_id, repetition)
                    ] = value
    trials: list[dict[str, Any]] = []
    for group_id, candidate_id, repetition in pairs:
        pair_digest = sha256_json(
            {
                "campaignId": campaign_id,
                "workloadGroupId": group_id,
                "candidateId": candidate_id,
                "repetition": repetition,
            }
        )[:20]
        pair_id = f"pair-v0-{pair_digest}"
        roles = ["default", "candidate"]
        candidate_first = (
            candidate_first_by_coordinate[(group_id, candidate_id, repetition)]
            if balanced_role_order
            else bool(generator.randrange(2))
        )
        if candidate_first:
            roles.reverse()
        for role in roles:
            ordinal = len(trials)
            trial_digest = sha256_json(
                {"pairId": pair_id, "configurationRole": role}
            )[:20]
            trials.append(
                {
                    "ordinal": ordinal,
                    "trialId": f"confirmation-trial-v0-{trial_digest}",
                    "pairId": pair_id,
                    "workloadGroupId": group_id,
                    "candidateId": candidate_id,
                    "configurationRole": role,
                    "repetition": repetition,
                }
            )

    schedule: dict[str, Any] = {
        "schemaVersion": SCHEDULE_SCHEMA_VERSION,
        "campaignId": campaign_id,
        "policy": (
            BALANCED_CONFIRMATION_POLICY
            if balanced_role_order
            else CONFIRMATION_POLICY
        ),
        "seed": seed,
        "defaultCandidateId": default_candidate_id,
        "repetitions": repetitions,
        "workloadGroupIds": groups,
        "candidates": [
            {
                "candidateId": candidate_id,
                "defaultControl": candidate_id in default_controls,
            }
            for candidate_id in candidates
        ],
        "trials": trials,
    }
    schedule["scheduleSha256"] = sha256_json(schedule)
    validate_confirmation_schedule(schedule)
    return schedule


def validate_confirmation_schedule(schedule: Mapping[str, Any]) -> None:
    _exact_fields(
        schedule,
        {
            "schemaVersion",
            "campaignId",
            "policy",
            "seed",
            "defaultCandidateId",
            "repetitions",
            "workloadGroupIds",
            "candidates",
            "trials",
            "scheduleSha256",
        },
        "confirmation schedule",
    )
    if schedule.get("schemaVersion") != SCHEDULE_SCHEMA_VERSION:
        raise ExperimentDesignError("unsupported confirmation schedule schema")
    policy = schedule.get("policy")
    if policy not in {CONFIRMATION_POLICY, BALANCED_CONFIRMATION_POLICY}:
        raise ExperimentDesignError("unsupported confirmation schedule policy")
    _string(schedule, "campaignId", "confirmation schedule")
    default_candidate_id = _string(
        schedule, "defaultCandidateId", "confirmation schedule"
    )
    _non_negative_int(schedule.get("seed"), "confirmation schedule seed")
    repetitions = schedule.get("repetitions")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 2:
        raise ExperimentDesignError("confirmation repetitions must be at least two")
    if policy == BALANCED_CONFIRMATION_POLICY and repetitions % 2:
        raise ExperimentDesignError(
            "balanced confirmation requires an even repetition count"
        )
    groups = schedule.get("workloadGroupIds")
    if not isinstance(groups, list) or not groups or len(set(groups)) != len(groups):
        raise ExperimentDesignError("confirmation workload groups must be unique")
    candidates = schedule.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ExperimentDesignError("confirmation candidates must be non-empty")
    candidate_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ExperimentDesignError("confirmation candidate must be an object")
        _exact_fields(candidate, {"candidateId", "defaultControl"}, "candidate")
        candidate_id = _string(candidate, "candidateId", "candidate")
        if candidate_id == default_candidate_id or candidate_id in candidate_ids:
            raise ExperimentDesignError("confirmation candidate IDs must be unique")
        if not isinstance(candidate.get("defaultControl"), bool):
            raise ExperimentDesignError("candidate defaultControl must be boolean")
        candidate_ids.add(candidate_id)

    expected_digest = schedule.get("scheduleSha256")
    without_digest = dict(schedule)
    without_digest.pop("scheduleSha256", None)
    if expected_digest != sha256_json(without_digest):
        raise ExperimentDesignError("confirmation schedule digest mismatch")

    trials = schedule.get("trials")
    expected_pairs = len(groups) * len(candidate_ids) * repetitions
    if not isinstance(trials, list) or len(trials) != expected_pairs * 2:
        raise ExperimentDesignError("confirmation schedule has the wrong trial count")
    seen_trial_ids: set[str] = set()
    seen_coordinates: set[tuple[str, str, int]] = set()
    candidate_first_counts: dict[tuple[str, str], int] = defaultdict(int)
    for pair_index in range(expected_pairs):
        pair_trials = trials[pair_index * 2 : pair_index * 2 + 2]
        if [trial.get("ordinal") for trial in pair_trials] != [
            pair_index * 2,
            pair_index * 2 + 1,
        ]:
            raise ExperimentDesignError("confirmation ordinals are not contiguous")
        if len({trial.get("pairId") for trial in pair_trials}) != 1:
            raise ExperimentDesignError("confirmation pair is not adjacent")
        if {trial.get("configurationRole") for trial in pair_trials} != {
            "default",
            "candidate",
        }:
            raise ExperimentDesignError("confirmation pair must contain both roles")
        coordinates = {
            (
                trial.get("workloadGroupId"),
                trial.get("candidateId"),
                trial.get("repetition"),
            )
            for trial in pair_trials
        }
        if len(coordinates) != 1:
            raise ExperimentDesignError("confirmation pair coordinates differ")
        coordinate = next(iter(coordinates))
        group_id, candidate_id, repetition = coordinate
        if group_id not in groups or candidate_id not in candidate_ids:
            raise ExperimentDesignError("confirmation pair references an unknown ID")
        if (
            isinstance(repetition, bool)
            or not isinstance(repetition, int)
            or not 0 <= repetition < repetitions
        ):
            raise ExperimentDesignError("confirmation pair has invalid repetition")
        if coordinate in seen_coordinates:
            raise ExperimentDesignError("duplicate confirmation pair coordinate")
        seen_coordinates.add(coordinate)
        if pair_trials[0].get("configurationRole") == "candidate":
            candidate_first_counts[(group_id, candidate_id)] += 1
        for trial in pair_trials:
            trial_id = trial.get("trialId")
            if not isinstance(trial_id, str) or not trial_id or trial_id in seen_trial_ids:
                raise ExperimentDesignError("confirmation trial IDs must be unique")
            seen_trial_ids.add(trial_id)

    if policy == BALANCED_CONFIRMATION_POLICY:
        expected_candidate_first = repetitions // 2
        for group_id in groups:
            for candidate_id in candidate_ids:
                if (
                    candidate_first_counts[(group_id, candidate_id)]
                    != expected_candidate_first
                ):
                    raise ExperimentDesignError(
                        "balanced confirmation has an unequal within-pair role order"
                    )


def summarize_grouped_confirmation(
    schedule: Mapping[str, Any],
    observations: Mapping[str, Any],
    *,
    bootstrap_seed: int,
    bootstrap_samples: int = 10_000,
    artifact_directory: Path | None = None,
) -> dict[str, Any]:
    """Summarize matched repetitions, then bootstrap independent groups."""

    validate_confirmation_schedule(schedule)
    _non_negative_int(bootstrap_seed, "bootstrap seed")
    if bootstrap_samples < 100:
        raise ExperimentDesignError("bootstrap requires at least 100 samples")
    _exact_fields(
        observations,
        {
            "schemaVersion",
            "scheduleSha256",
            "scoreAggregation",
            "complete",
            "results",
        },
        "confirmation observations",
    )
    if observations.get("schemaVersion") != OBSERVATIONS_SCHEMA_VERSION:
        raise ExperimentDesignError("unsupported confirmation observations schema")
    if observations.get("scheduleSha256") != schedule["scheduleSha256"]:
        raise ExperimentDesignError("observations belong to another schedule")
    if observations.get("scoreAggregation") not in SUPPORTED_SCORE_AGGREGATIONS:
        raise ExperimentDesignError("observations use an unsupported aggregation")
    if observations.get("complete") is not True:
        raise ExperimentDesignError("confirmation observations are incomplete")
    results = observations.get("results")
    if not isinstance(results, list):
        raise ExperimentDesignError("confirmation results must be an array")
    results_by_pair: dict[str, Mapping[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ExperimentDesignError("confirmation result must be an object")
        _exact_fields(
            result,
            {
                "pairId",
                "candidateId",
                "workloadGroupId",
                "repetition",
                "defaultReport",
                "candidateReport",
                "reward",
                "eligible",
                "wrongAnswerCount",
                "timeoutCount",
                "failureCount",
            },
            "confirmation result",
        )
        pair_id = _string(result, "pairId", "confirmation result")
        _string(result, "candidateId", "confirmation result")
        _string(result, "workloadGroupId", "confirmation result")
        _non_negative_int(result.get("repetition"), "confirmation repetition")
        if pair_id in results_by_pair:
            raise ExperimentDesignError(f"duplicate confirmation result: {pair_id}")
        reward = result.get("reward")
        if (
            isinstance(reward, bool)
            or not isinstance(reward, (int, float))
            or not math.isfinite(float(reward))
        ):
            raise ExperimentDesignError("confirmation reward must be finite")
        for binding_name in ("defaultReport", "candidateReport"):
            binding = result.get(binding_name)
            if not isinstance(binding, dict):
                raise ExperimentDesignError(f"{binding_name} must be an object")
            _exact_fields(binding, {"path", "sha256"}, binding_name)
            _string(binding, "path", binding_name)
            digest = binding.get("sha256")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ExperimentDesignError(f"{binding_name} has invalid sha256")
            if artifact_directory is not None:
                _resolve_binding(artifact_directory, binding, binding_name)
        if not isinstance(result.get("eligible"), bool):
            raise ExperimentDesignError("confirmation eligible must be boolean")
        for field in ("wrongAnswerCount", "timeoutCount", "failureCount"):
            _non_negative_int(result.get(field), f"confirmation {field}")
        results_by_pair[pair_id] = result

    pair_trials: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for trial in schedule["trials"]:
        pair_trials[trial["pairId"]].append(trial)
    if set(results_by_pair) != set(pair_trials):
        missing = sorted(set(pair_trials) - set(results_by_pair))
        extra = sorted(set(results_by_pair) - set(pair_trials))
        raise ExperimentDesignError(
            f"confirmation results do not match schedule: missing={missing}, extra={extra}"
        )
    rewards: dict[tuple[str, str], list[float]] = defaultdict(list)
    eligible_by_candidate: dict[str, bool] = {
        candidate["candidateId"]: True for candidate in schedule["candidates"]
    }
    for pair_id, trials in pair_trials.items():
        candidate_trial = next(
            trial for trial in trials if trial["configurationRole"] == "candidate"
        )
        result = results_by_pair[pair_id]
        candidate_id = candidate_trial["candidateId"]
        if (
            result["candidateId"] != candidate_id
            or result["workloadGroupId"] != candidate_trial["workloadGroupId"]
            or result["repetition"] != candidate_trial["repetition"]
        ):
            raise ExperimentDesignError("confirmation result coordinates differ from schedule")
        failure_free = (
            result["eligible"]
            and result["wrongAnswerCount"] == 0
            and result["timeoutCount"] == 0
            and result["failureCount"] == 0
        )
        eligible_by_candidate[candidate_id] &= failure_free
        rewards[(candidate_id, candidate_trial["workloadGroupId"])].append(
            float(result["reward"])
        )

    control_by_candidate = {
        candidate["candidateId"]: candidate["defaultControl"]
        for candidate in schedule["candidates"]
    }
    summary_results = []
    for candidate_id in sorted(control_by_candidate):
        group_rewards = []
        paired_repetition_count = 0
        for group_id in schedule["workloadGroupIds"]:
            repetitions = rewards[(candidate_id, group_id)]
            if len(repetitions) != schedule["repetitions"]:
                raise ExperimentDesignError("confirmation group has missing repetitions")
            paired_repetition_count += len(repetitions)
            group_rewards.append(
                {
                    "workloadGroupId": group_id,
                    "medianLogSpeedup": statistics.median(repetitions),
                    "repetitions": len(repetitions),
                }
            )
        values = [item["medianLogSpeedup"] for item in group_rewards]
        mean_reward = statistics.fmean(values)
        low, high = _bootstrap_mean_interval(
            values, seed=bootstrap_seed, samples=bootstrap_samples
        )
        summary_results.append(
            {
                "candidateId": candidate_id,
                "defaultControl": control_by_candidate[candidate_id],
                "eligible": eligible_by_candidate[candidate_id],
                "workloadGroupCount": len(values),
                "pairedRepetitionCount": paired_repetition_count,
                "meanLogSpeedup": mean_reward,
                "medianLogSpeedup": statistics.median(values),
                "geometricSpeedup": math.exp(mean_reward),
                "relativeImprovement": 1.0 - math.exp(-mean_reward),
                "positiveGroupCount": sum(value > 0 for value in values),
                "bootstrap95MeanLogSpeedup": {"low": low, "high": high},
                "groupRewards": group_rewards,
            }
        )
    return {
        "schemaVersion": SUMMARY_SCHEMA_VERSION,
        "scheduleSha256": schedule["scheduleSha256"],
        "scoreAggregation": observations["scoreAggregation"],
        "bootstrapSeed": bootstrap_seed,
        "results": summary_results,
    }


def _report_duration_total(
    report: Mapping[str, Any], field: str, subject: str
) -> tuple[int, set[tuple[str, int]]]:
    if report.get("schemaVersion") != "tsa-cvc5-profile-replay-report-v0":
        raise ExperimentDesignError(f"{subject} has unsupported report schema")
    results = report.get("results")
    if not isinstance(results, list) or not results:
        raise ExperimentDesignError(f"{subject} has no replay results")
    total = 0
    coordinates: set[tuple[str, int]] = set()
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise ExperimentDesignError(f"{subject} result {index} is not an object")
        profile_id = result.get("profileId")
        repetition = result.get("repetition")
        duration = result.get(field)
        if not isinstance(profile_id, str) or not profile_id:
            raise ExperimentDesignError(f"{subject} result {index} has no profileId")
        if (
            isinstance(repetition, bool)
            or not isinstance(repetition, int)
            or repetition < 0
        ):
            raise ExperimentDesignError(
                f"{subject} result {index} has invalid repetition"
            )
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or duration < 0
        ):
            raise ExperimentDesignError(
                f"{subject} result {index} has invalid {field}"
            )
        coordinate = (profile_id, repetition)
        if coordinate in coordinates:
            raise ExperimentDesignError(
                f"{subject} contains duplicate replay coordinate {coordinate}"
            )
        coordinates.add(coordinate)
        total += duration
    if total <= 0:
        raise ExperimentDesignError(f"{subject} has zero total {field}")
    return total, coordinates


def _noise_metric_summary(
    values: Sequence[float],
    *,
    bootstrap_seed: int,
    bootstrap_samples: int,
    max_absolute_median: float,
    max_interval_width: float,
) -> dict[str, Any]:
    if len(values) < 2:
        raise ExperimentDesignError("noise calibration requires at least two pairs")
    low, high = _bootstrap_mean_interval(
        values, seed=bootstrap_seed, samples=bootstrap_samples
    )
    median = statistics.median(values)
    interval_width = high - low
    interval_contains_zero = low <= 0 <= high
    passes_gate = (
        interval_contains_zero
        and abs(median) <= max_absolute_median
        and interval_width <= max_interval_width
    )
    return {
        "pairedValues": list(values),
        "meanLogSpeedup": statistics.fmean(values),
        "medianLogSpeedup": median,
        "geometricSpeedup": math.exp(statistics.fmean(values)),
        "sampleStandardDeviation": statistics.stdev(values),
        "medianAbsoluteDeviation": statistics.median(
            abs(value - median) for value in values
        ),
        "bootstrap95MeanLogSpeedup": {"low": low, "high": high},
        "intervalWidth": interval_width,
        "intervalContainsZero": interval_contains_zero,
        "absoluteMedianLogSpeedup": abs(median),
        "passesGate": passes_gate,
    }


def summarize_default_noise_calibration(
    schedule: Mapping[str, Any],
    observations: Mapping[str, Any],
    *,
    target_minimum_relative_improvement: float,
    score_aggregation: str,
    bootstrap_seed: int,
    artifact_directory: Path,
    bootstrap_samples: int = 10_000,
    minimum_paired_repetitions_per_group: int = 12,
) -> dict[str, Any]:
    """Estimate default/default measurement noise from matched repetitions.

    Unlike final confirmation, calibration resamples measured pairs rather than
    workload groups.  This keeps the interval meaningful when validation has a
    single independent workload group.  The replay proxy remains the primary
    search objective; full solver and wall durations are reported as independent
    diagnostics so objective noise is not confused with end-to-end drift.
    """

    if (
        isinstance(target_minimum_relative_improvement, bool)
        or not isinstance(target_minimum_relative_improvement, (int, float))
        or not 0 < float(target_minimum_relative_improvement) < 1
    ):
        raise ExperimentDesignError(
            "target minimum relative improvement must be between zero and one"
        )
    if score_aggregation not in SUPPORTED_SCORE_AGGREGATIONS:
        raise ExperimentDesignError("unsupported noise calibration aggregation")
    _non_negative_int(bootstrap_seed, "bootstrap seed")
    if bootstrap_samples < 100:
        raise ExperimentDesignError("bootstrap requires at least 100 samples")
    if minimum_paired_repetitions_per_group < 2:
        raise ExperimentDesignError("minimum paired repetitions must be at least two")
    if schedule.get("policy") != BALANCED_CONFIRMATION_POLICY:
        raise ExperimentDesignError(
            "default noise calibration requires balanced within-pair role order"
        )
    candidates = schedule.get("candidates")
    if (
        not isinstance(candidates, list)
        or len(candidates) != 1
        or candidates[0].get("defaultControl") is not True
    ):
        raise ExperimentDesignError(
            "default noise calibration requires exactly one default control"
        )

    grouped = summarize_grouped_confirmation(
        schedule,
        observations,
        bootstrap_seed=bootstrap_seed,
        bootstrap_samples=bootstrap_samples,
        artifact_directory=artifact_directory,
    )
    control = grouped["results"][0]
    if observations["scoreAggregation"] != score_aggregation:
        raise ExperimentDesignError(
            "noise observations use another campaign aggregation"
        )

    ordered_results = sorted(
        observations["results"],
        key=lambda item: (
            item["workloadGroupId"],
            item["repetition"],
            item["pairId"],
        ),
    )
    proxy_values: list[float] = []
    solver_values: list[float] = []
    wall_values: list[float] = []
    for result in ordered_results:
        default_path = _resolve_binding(
            artifact_directory, result["defaultReport"], "default report"
        )
        candidate_path = _resolve_binding(
            artifact_directory, result["candidateReport"], "candidate report"
        )
        default_report = load_json(default_path)
        candidate_report = load_json(candidate_path)
        proxy_values.append(float(result["reward"]))
        for field, values in (
            ("solverOperationDurationNanos", solver_values),
            ("wallDurationNanos", wall_values),
        ):
            default_total, default_coordinates = _report_duration_total(
                default_report, field, "default report"
            )
            candidate_total, candidate_coordinates = _report_duration_total(
                candidate_report, field, "candidate report"
            )
            if default_coordinates != candidate_coordinates:
                raise ExperimentDesignError(
                    f"default and candidate reports differ for {field}"
                )
            values.append(math.log(default_total / candidate_total))

    target_log_speedup = -math.log1p(-float(target_minimum_relative_improvement))
    max_absolute_median = target_log_speedup / 4
    max_interval_width = target_log_speedup
    metrics = {
        "replayProxyLogSpeedup": _noise_metric_summary(
            proxy_values,
            bootstrap_seed=bootstrap_seed,
            bootstrap_samples=bootstrap_samples,
            max_absolute_median=max_absolute_median,
            max_interval_width=max_interval_width,
        ),
        "solverOperationLogSpeedup": _noise_metric_summary(
            solver_values,
            bootstrap_seed=bootstrap_seed,
            bootstrap_samples=bootstrap_samples,
            max_absolute_median=max_absolute_median,
            max_interval_width=max_interval_width,
        ),
        "wallLogSpeedup": _noise_metric_summary(
            wall_values,
            bootstrap_seed=bootstrap_seed,
            bootstrap_samples=bootstrap_samples,
            max_absolute_median=max_absolute_median,
            max_interval_width=max_interval_width,
        ),
    }
    total_wrong_answers = sum(item["wrongAnswerCount"] for item in ordered_results)
    total_timeouts = sum(item["timeoutCount"] for item in ordered_results)
    total_failures = sum(item["failureCount"] for item in ordered_results)
    all_pairs_eligible = all(item["eligible"] for item in ordered_results)
    correctness_passed = (
        all_pairs_eligible
        and total_wrong_answers == 0
        and total_timeouts == 0
        and total_failures == 0
        and control["eligible"]
    )
    enough_repetitions = (
        schedule["repetitions"] >= minimum_paired_repetitions_per_group
    )
    failed_checks: list[str] = []
    if not correctness_passed:
        failed_checks.append("correctness-and-replay-integrity")
    if not enough_repetitions:
        failed_checks.append("minimum-paired-repetitions")
    for name, metric in metrics.items():
        if not metric["intervalContainsZero"]:
            failed_checks.append(f"{name}:interval-does-not-contain-zero")
        if metric["absoluteMedianLogSpeedup"] > max_absolute_median:
            failed_checks.append(f"{name}:median-bias-too-large")
        if metric["intervalWidth"] > max_interval_width:
            failed_checks.append(f"{name}:interval-too-wide")

    return {
        "schemaVersion": NOISE_SUMMARY_SCHEMA_VERSION,
        "scheduleSha256": schedule["scheduleSha256"],
        "candidateId": candidates[0]["candidateId"],
        "workloadGroupIds": list(schedule["workloadGroupIds"]),
        "pairedRepetitionCount": len(ordered_results),
        "bootstrapSeed": bootstrap_seed,
        "targetMinimumRelativeImprovement": float(
            target_minimum_relative_improvement
        ),
        "scoreAggregation": score_aggregation,
        "targetLogSpeedup": target_log_speedup,
        "thresholds": {
            "minimumPairedRepetitionsPerGroup": (
                minimum_paired_repetitions_per_group
            ),
            "maxAbsoluteMedianLogSpeedup": max_absolute_median,
            "maxBootstrap95IntervalWidth": max_interval_width,
            "requireIntervalContainsZero": True,
        },
        "correctness": {
            "allPairsEligible": all_pairs_eligible,
            "wrongAnswerCount": total_wrong_answers,
            "timeoutCount": total_timeouts,
            "failureCount": total_failures,
            "passed": correctness_passed,
        },
        "metrics": metrics,
        "gate": {"passed": not failed_checks, "failedChecks": failed_checks},
    }


def rescore_default_noise_reports(
    schedule: Mapping[str, Any],
    source_observations: Mapping[str, Any],
    *,
    source_directory: Path,
    output_directory: Path,
    score_aggregation: str,
    target_minimum_relative_improvement: float,
    bootstrap_seed: int,
    timeout_penalty: float = 10.0,
    reward_clip: float = 5.0,
) -> dict[str, Any]:
    """Re-score completed reports without claiming an independent calibration."""

    validate_confirmation_schedule(schedule)
    if score_aggregation not in SUPPORTED_SCORE_AGGREGATIONS:
        raise ExperimentDesignError("unsupported exploratory score aggregation")
    if source_observations.get("scheduleSha256") != schedule["scheduleSha256"]:
        raise ExperimentDesignError("source observations belong to another schedule")
    if source_observations.get("complete") is not True:
        raise ExperimentDesignError("source observations are incomplete")
    source_results = source_observations.get("results")
    if not isinstance(source_results, list):
        raise ExperimentDesignError("source observation results must be an array")
    expected_pair_ids = {
        trial["pairId"] for trial in schedule["trials"]
    }
    if {item.get("pairId") for item in source_results} != expected_pair_ids:
        raise ExperimentDesignError("source observations do not match the schedule")

    output_directory.mkdir(parents=True, exist_ok=True)
    rescored_results = []
    for source in source_results:
        default_path = _resolve_binding(
            source_directory, source["defaultReport"], "source default report"
        )
        candidate_path = _resolve_binding(
            source_directory, source["candidateReport"], "source candidate report"
        )
        score = score_reports(
            load_json(default_path),
            load_json(candidate_path),
            timeout_penalty=timeout_penalty,
            reward_clip=reward_clip,
            aggregation=score_aggregation,
        )
        rescored_results.append(
            {
                "pairId": source["pairId"],
                "candidateId": source["candidateId"],
                "workloadGroupId": source["workloadGroupId"],
                "repetition": source["repetition"],
                "defaultReport": {
                    "path": os.path.relpath(default_path, output_directory),
                    "sha256": sha256_file(default_path),
                },
                "candidateReport": {
                    "path": os.path.relpath(candidate_path, output_directory),
                    "sha256": sha256_file(candidate_path),
                },
                "reward": score["reward"],
                "eligible": score["eligible"],
                "wrongAnswerCount": score["totals"]["wrongAnswerCount"],
                "timeoutCount": score["totals"]["knownStatusTimeoutCount"],
                "failureCount": score["totals"]["failedReplayCount"],
            }
        )
    observations = {
        "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
        "scheduleSha256": schedule["scheduleSha256"],
        "scoreAggregation": score_aggregation,
        "complete": True,
        "results": rescored_results,
    }
    schedule_path = output_directory / "source-schedule-v0.json"
    observations_path = output_directory / "observations-v0.json"
    summary_path = output_directory / "exploratory-noise-summary-v0.json"
    write_json(schedule_path, schedule)
    write_json(observations_path, observations)
    summary = summarize_default_noise_calibration(
        schedule,
        observations,
        target_minimum_relative_improvement=target_minimum_relative_improvement,
        score_aggregation=score_aggregation,
        bootstrap_seed=bootstrap_seed,
        artifact_directory=output_directory,
    )
    write_json(summary_path, summary)
    return summary


def _bootstrap_mean_interval(
    values: Sequence[float], *, seed: int, samples: int
) -> tuple[float, float]:
    if not values:
        raise ExperimentDesignError("cannot bootstrap an empty group sample")
    if len(values) == 1:
        return float(values[0]), float(values[0])
    generator = random.Random(seed)
    estimates = sorted(
        statistics.fmean(generator.choice(values) for _ in values)
        for _ in range(samples)
    )
    return estimates[int(samples * 0.025)], estimates[int(samples * 0.975) - 1]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate_corpus = commands.add_parser("validate-corpus")
    validate_corpus.add_argument("--manifest", type=Path, required=True)

    build_corpus = commands.add_parser("build-corpus")
    build_corpus.add_argument("--corpus-id", required=True)
    build_corpus.add_argument("--solver-version", required=True)
    build_corpus.add_argument("--parameter-space-id", required=True)
    build_corpus.add_argument("--grouping-key", required=True)
    build_corpus.add_argument(
        "--group",
        nargs=4,
        action="append",
        metavar=("PARTITION", "GROUP_ID", "LEAKAGE_GROUP_ID", "BASELINE_MANIFEST"),
        required=True,
    )
    build_corpus.add_argument("--output", type=Path, required=True)

    validate_campaign = commands.add_parser("validate-campaign")
    validate_campaign.add_argument("--manifest", type=Path, required=True)

    build_campaign = commands.add_parser("build-campaign")
    build_campaign.add_argument("--campaign-id", required=True)
    build_campaign.add_argument("--corpus", type=Path, required=True)
    build_campaign.add_argument("--default-candidate-id", required=True)
    build_campaign.add_argument("--seed", type=int, action="append", required=True)
    build_campaign.add_argument("--wall-clock-seconds", type=float, required=True)
    build_campaign.add_argument("--maximum-evaluations", type=int, required=True)
    build_campaign.add_argument(
        "--per-trial-timeout-seconds", type=float, required=True
    )
    build_campaign.add_argument(
        "--confirmation-repetitions", type=int, required=True
    )
    build_campaign.add_argument("--confirmation-seed", type=int, required=True)
    build_campaign.add_argument(
        "--minimum-relative-improvement", type=float, required=True
    )
    build_campaign.add_argument(
        "--minimum-positive-group-fraction", type=float, required=True
    )
    build_campaign.add_argument(
        "--objective",
        choices=sorted(SUPPORTED_SCORE_AGGREGATIONS),
        default=PROFILE_MACRO_AGGREGATION,
    )
    build_campaign.add_argument("--output", type=Path, required=True)

    schedule = commands.add_parser("build-confirmation-schedule")
    schedule.add_argument("--campaign", type=Path, required=True)
    schedule.add_argument(
        "--partition", choices=("validation", "test"), default="test"
    )
    schedule.add_argument("--candidate", action="append", required=True)
    schedule.add_argument("--default-control", action="append", default=[])
    schedule.add_argument("--repetitions", type=int)
    schedule.add_argument("--balanced-role-order", action="store_true")
    schedule.add_argument("--output", type=Path, required=True)

    validate_schedule = commands.add_parser("validate-confirmation-schedule")
    validate_schedule.add_argument("--schedule", type=Path, required=True)

    summarize = commands.add_parser("summarize-confirmation")
    summarize.add_argument("--schedule", type=Path, required=True)
    summarize.add_argument("--observations", type=Path, required=True)
    summarize.add_argument("--bootstrap-seed", type=int, required=True)
    summarize.add_argument("--output", type=Path, required=True)

    summarize_noise = commands.add_parser("summarize-default-noise")
    summarize_noise.add_argument("--campaign", type=Path, required=True)
    summarize_noise.add_argument("--schedule", type=Path, required=True)
    summarize_noise.add_argument("--observations", type=Path, required=True)
    summarize_noise.add_argument("--bootstrap-seed", type=int, required=True)
    summarize_noise.add_argument("--output", type=Path, required=True)

    rescore_noise = commands.add_parser("rescore-default-noise")
    rescore_noise.add_argument("--schedule", type=Path, required=True)
    rescore_noise.add_argument("--observations", type=Path, required=True)
    rescore_noise.add_argument(
        "--score-aggregation",
        choices=sorted(SUPPORTED_SCORE_AGGREGATIONS),
        required=True,
    )
    rescore_noise.add_argument(
        "--target-minimum-relative-improvement", type=float, required=True
    )
    rescore_noise.add_argument("--bootstrap-seed", type=int, required=True)
    rescore_noise.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        if arguments.command == "validate-corpus":
            corpus = load_workload_corpus(arguments.manifest)
            print(f"validated corpus {corpus['corpusId']}")
        elif arguments.command == "build-corpus":
            corpus = build_workload_corpus(
                corpus_id=arguments.corpus_id,
                solver_version=arguments.solver_version,
                parameter_space_id=arguments.parameter_space_id,
                grouping_key=arguments.grouping_key,
                groups=[
                    (partition, group_id, leakage_group_id, Path(baseline_manifest))
                    for partition, group_id, leakage_group_id, baseline_manifest
                    in arguments.group
                ],
                output_path=arguments.output,
            )
            print(
                f"wrote corpus {corpus['corpusId']} with "
                f"{len(corpus['groups'])} groups to {arguments.output}"
            )
        elif arguments.command == "validate-campaign":
            campaign, _ = load_configurator_campaign(arguments.manifest)
            print(f"validated campaign {campaign['campaignId']}")
        elif arguments.command == "build-campaign":
            campaign = build_configurator_campaign(
                campaign_id=arguments.campaign_id,
                corpus_path=arguments.corpus,
                default_candidate_id=arguments.default_candidate_id,
                seeds=arguments.seed,
                wall_clock_seconds=arguments.wall_clock_seconds,
                maximum_evaluations=arguments.maximum_evaluations,
                per_trial_timeout_seconds=arguments.per_trial_timeout_seconds,
                confirmation_repetitions=arguments.confirmation_repetitions,
                confirmation_seed=arguments.confirmation_seed,
                minimum_relative_improvement=arguments.minimum_relative_improvement,
                minimum_positive_group_fraction=(
                    arguments.minimum_positive_group_fraction
                ),
                output_path=arguments.output,
                objective=arguments.objective,
            )
            print(
                f"wrote campaign {campaign['campaignId']} with "
                f"{len(campaign['algorithms'][0]['seeds'])} seeds per algorithm"
            )
        elif arguments.command == "build-confirmation-schedule":
            campaign, corpus = load_configurator_campaign(arguments.campaign)
            workload_groups = [
                group["groupId"]
                for group in corpus["groups"]
                if group["partition"] == arguments.partition
            ]
            schedule = build_confirmation_schedule(
                campaign_id=campaign["campaignId"],
                default_candidate_id=campaign["defaultCandidateId"],
                workload_group_ids=workload_groups,
                candidate_ids=arguments.candidate,
                repetitions=(
                    arguments.repetitions
                    if arguments.repetitions is not None
                    else campaign["confirmation"]["repetitions"]
                ),
                seed=campaign["confirmation"]["scheduleSeed"],
                default_control_ids=arguments.default_control,
                balanced_role_order=arguments.balanced_role_order,
            )
            write_json(arguments.output, schedule)
            print(
                f"wrote {len(schedule['trials'])} confirmation trials to "
                f"{arguments.output}"
            )
        elif arguments.command == "validate-confirmation-schedule":
            schedule = load_json(arguments.schedule)
            validate_confirmation_schedule(schedule)
            print(
                f"validated confirmation schedule with "
                f"{len(schedule['trials'])} trials"
            )
        elif arguments.command == "summarize-confirmation":
            summary = summarize_grouped_confirmation(
                load_json(arguments.schedule),
                load_json(arguments.observations),
                bootstrap_seed=arguments.bootstrap_seed,
                artifact_directory=arguments.observations.resolve().parent,
            )
            write_json(arguments.output, summary)
            print(f"wrote grouped confirmation summary to {arguments.output}")
        elif arguments.command == "summarize-default-noise":
            campaign, _ = load_configurator_campaign(arguments.campaign)
            schedule = load_json(arguments.schedule)
            if schedule.get("campaignId") != campaign["campaignId"]:
                raise ExperimentDesignError(
                    "noise schedule belongs to another campaign"
                )
            summary = summarize_default_noise_calibration(
                schedule,
                load_json(arguments.observations),
                target_minimum_relative_improvement=campaign["gate"][
                    "minimumRelativeImprovement"
                ],
                score_aggregation=campaign["objective"],
                bootstrap_seed=arguments.bootstrap_seed,
                artifact_directory=arguments.observations.resolve().parent,
            )
            write_json(arguments.output, summary)
            print(
                "wrote default/default noise summary to "
                f"{arguments.output}; gatePassed={summary['gate']['passed']}"
            )
        else:
            summary = rescore_default_noise_reports(
                load_json(arguments.schedule),
                load_json(arguments.observations),
                source_directory=arguments.observations.resolve().parent,
                output_directory=arguments.output_dir.resolve(),
                score_aggregation=arguments.score_aggregation,
                target_minimum_relative_improvement=(
                    arguments.target_minimum_relative_improvement
                ),
                bootstrap_seed=arguments.bootstrap_seed,
            )
            print(
                "wrote exploratory default/default rescore to "
                f"{arguments.output_dir}; gatePassed={summary['gate']['passed']}"
            )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as failure:
        print(f"experiment design failed: {failure}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
