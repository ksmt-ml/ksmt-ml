"""Materialize, inspect, and summarize a multi-seed configurator campaign."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from ksmt_ml.evaluator import load_json, sha256_file, write_json
from ksmt_ml.experiment_design import (
    ExperimentDesignError,
    build_confirmation_schedule,
    load_configurator_campaign,
    validate_confirmation_schedule,
    validate_configurator_campaign,
    validate_workload_corpus,
)


WORKSPACE_SCHEMA_VERSION = "cvc5-campaign-workspace-v0"
FINALIST_INDEX_SCHEMA_VERSION = "cvc5-finalist-index-v0"
ANYTIME_SUMMARY_SCHEMA_VERSION = "cvc5-multi-seed-anytime-summary-v0"
CANDIDATE_INDEX_SCHEMA_VERSION = "cvc5-confirmation-candidate-index-v0"
ALGORITHMS = ("random", "evolution", "smac")
DEFAULT_TIME_FRACTIONS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)
QUALIFICATION_POLICY = "complete-development-workload-success-v0"
QUALIFIED = "QUALIFIED"
NO_FEASIBLE_CANDIDATE = "NO_FEASIBLE_CANDIDATE"


def _relative(path: Path, base: Path) -> str:
    return os.path.relpath(path.resolve(), base.resolve())


def _binding(path: Path, base: Path) -> dict[str, str]:
    return {"path": _relative(path, base), "sha256": sha256_file(path)}


def _resolve(base: Path, path: str) -> Path:
    value = Path(path)
    if not value.is_absolute():
        value = base / value
    return value.resolve()


def initialize_campaign_workspace(
    campaign_path: Path, output_directory: Path
) -> dict[str, Any]:
    """Create an idempotent directory skeleton with portable frozen design files."""

    source_campaign, source_corpus = load_configurator_campaign(campaign_path)
    output_directory = output_directory.resolve()
    design_directory = output_directory / "design"
    design_directory.mkdir(parents=True, exist_ok=True)
    source_corpus_path = _resolve(
        campaign_path.resolve().parent, source_campaign["corpus"]["path"]
    )

    corpus = json.loads(json.dumps(source_corpus))
    for group in corpus["groups"]:
        source_baseline = _resolve(
            source_corpus_path.parent, group["baselineManifest"]["path"]
        )
        group["baselineManifest"] = _binding(source_baseline, design_directory)
    corpus_path = design_directory / "corpus-v0.json"
    validate_workload_corpus(corpus, base_directory=design_directory)
    _write_or_verify(corpus_path, corpus, "workspace corpus")

    campaign = json.loads(json.dumps(source_campaign))
    campaign["corpus"] = {
        "path": corpus_path.name,
        "sha256": sha256_file(corpus_path),
        "corpusId": corpus["corpusId"],
    }
    campaign_path_in_workspace = design_directory / "campaign-v0.json"
    validate_configurator_campaign(campaign, base_directory=design_directory)
    _write_or_verify(campaign_path_in_workspace, campaign, "workspace campaign")

    run_slots = []
    algorithms = {item["name"]: item for item in campaign["algorithms"]}
    for algorithm in ALGORITHMS:
        for seed in algorithms[algorithm]["seeds"]:
            run_directory = output_directory / "runs" / algorithm / f"seed-{seed}"
            run_directory.mkdir(parents=True, exist_ok=True)
            run_slots.append(
                {
                    "algorithm": algorithm,
                    "seed": seed,
                    "path": _relative(run_directory, output_directory),
                }
            )
    for relative_directory in (
        "confirmation/finalists",
        "confirmation/run",
        "reports",
    ):
        (output_directory / relative_directory).mkdir(parents=True, exist_ok=True)
    workspace = {
        "schemaVersion": WORKSPACE_SCHEMA_VERSION,
        "campaignId": campaign["campaignId"],
        "campaign": _binding(campaign_path_in_workspace, output_directory),
        "corpus": _binding(corpus_path, output_directory),
        "runSlots": run_slots,
    }
    workspace_path = output_directory / "workspace-v0.json"
    _write_or_verify(workspace_path, workspace, "campaign workspace")
    validate_campaign_workspace(workspace, base_directory=output_directory)
    return workspace


def _write_or_verify(path: Path, value: Mapping[str, Any], subject: str) -> None:
    if path.is_file():
        if load_json(path) != value:
            raise ExperimentDesignError(
                f"existing {subject} differs from the requested frozen design: {path}"
            )
        return
    write_json(path, value)


def validate_campaign_workspace(
    workspace: Mapping[str, Any], *, base_directory: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = {"schemaVersion", "campaignId", "campaign", "corpus", "runSlots"}
    if set(workspace) != expected or workspace.get("schemaVersion") != WORKSPACE_SCHEMA_VERSION:
        raise ExperimentDesignError("campaign workspace fields do not match v0")
    campaign_path = _validate_binding(
        workspace.get("campaign"), base_directory, "workspace campaign"
    )
    corpus_path = _validate_binding(
        workspace.get("corpus"), base_directory, "workspace corpus"
    )
    campaign = load_json(campaign_path)
    corpus = load_json(corpus_path)
    validate_configurator_campaign(campaign, base_directory=campaign_path.parent)
    validate_workload_corpus(corpus, base_directory=corpus_path.parent)
    if workspace.get("campaignId") != campaign.get("campaignId"):
        raise ExperimentDesignError("workspace campaignId mismatch")
    expected_slots = {
        (algorithm["name"], seed)
        for algorithm in campaign["algorithms"]
        for seed in algorithm["seeds"]
    }
    slots = workspace.get("runSlots")
    if not isinstance(slots, list):
        raise ExperimentDesignError("workspace runSlots must be an array")
    actual_slots: set[tuple[str, int]] = set()
    for slot in slots:
        if not isinstance(slot, dict) or set(slot) != {"algorithm", "seed", "path"}:
            raise ExperimentDesignError("workspace run slot is malformed")
        coordinate = (slot.get("algorithm"), slot.get("seed"))
        if coordinate in actual_slots:
            raise ExperimentDesignError("workspace contains a duplicate run slot")
        actual_slots.add(coordinate)
        run_path = _resolve(base_directory, slot.get("path", ""))
        if not run_path.is_dir():
            raise ExperimentDesignError(f"workspace run directory is missing: {run_path}")
    if actual_slots != expected_slots:
        raise ExperimentDesignError("workspace run slots differ from campaign seeds")
    return campaign, corpus


def _validate_binding(binding: Any, base: Path, subject: str) -> Path:
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise ExperimentDesignError(f"{subject} binding is malformed")
    path = _resolve(base, binding.get("path", ""))
    if not path.is_file() or sha256_file(path) != binding.get("sha256"):
        raise ExperimentDesignError(f"{subject} binding mismatch: {path}")
    return path


def load_campaign_workspace(
    workspace_directory: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = workspace_directory.resolve()
    workspace = load_json(root / "workspace-v0.json")
    campaign, corpus = validate_campaign_workspace(workspace, base_directory=root)
    return workspace, campaign, corpus


def collect_finalists(workspace_directory: Path) -> dict[str, Any]:
    """Qualify every seed result and materialize only eligible incumbents.

    Missing or development-ineligible incumbents are scientific outcomes, not
    corrupt runs.  They remain visible in ``slotResults`` but never reach the
    sealed-test confirmation schedule.
    """

    root = workspace_directory.resolve()
    workspace, campaign, corpus = load_campaign_workspace(root)
    development_baseline_digests = {
        group["baselineManifest"]["sha256"]
        for group in corpus["groups"]
        if group["partition"] == "development"
    }
    output_directory = root / "confirmation" / "finalists"
    output_directory.mkdir(parents=True, exist_ok=True)
    finalists = []
    slot_results = []
    selected_development_baseline: str | None = None
    for slot in workspace["runSlots"]:
        algorithm = slot["algorithm"]
        seed = slot["seed"]
        run_directory = _resolve(root, slot["path"])
        manifest_path = run_directory / "manifest.json"
        state_path = run_directory / "state.json"
        events_path = run_directory / "events.jsonl"
        for required in (manifest_path, state_path, events_path):
            if not required.is_file():
                raise ExperimentDesignError(
                    f"{algorithm} seed {seed} is incomplete: missing {required.name}"
                )
        manifest = load_json(manifest_path)
        state = load_json(state_path)
        _validate_run_identity(
            manifest,
            state,
            algorithm=algorithm,
            seed=seed,
            campaign=campaign,
            development_baseline_digests=development_baseline_digests,
        )
        run_development_baseline = manifest["experiment"]["evaluator"][
            "baselineManifestSha256"
        ]
        if selected_development_baseline is None:
            selected_development_baseline = run_development_baseline
        elif run_development_baseline != selected_development_baseline:
            raise ExperimentDesignError(
                "configurator runs used different development workloads"
            )
        source_run = {
            "path": _relative(run_directory, output_directory),
            "manifestSha256": sha256_file(manifest_path),
            "stateSha256": sha256_file(state_path),
            "eventsSha256": sha256_file(events_path),
        }
        expected_profile_ids = _development_profile_ids(manifest)
        incumbent = state.get("incumbent")
        candidate: dict[str, Any] | None = None
        cost: float | None = None
        qualification_reason = "NO_INCUMBENT"
        coverage = {
            "expectedProfileCount": len(expected_profile_ids),
            "observedProfileCount": 0,
            "successfulProfileCount": 0,
        }
        if algorithm == "smac":
            if isinstance(incumbent, dict) and isinstance(
                incumbent.get("candidate"), dict
            ):
                candidate = incumbent["candidate"]
                cost = _valid_development_cost(incumbent.get("cost"))
                coverage = _smac_candidate_coverage(
                    events_path,
                    candidate_id=candidate.get("candidateId"),
                    expected_profile_ids=expected_profile_ids,
                )
                qualification_reason = (
                    "FULL_DEVELOPMENT_WORKLOAD_SUCCESS"
                    if coverage["successfulProfileCount"]
                    == coverage["expectedProfileCount"]
                    and coverage["observedProfileCount"]
                    == coverage["expectedProfileCount"]
                    else "INCUMBENT_INELIGIBLE_ON_DEVELOPMENT"
                )
        else:
            if isinstance(incumbent, dict):
                trial_id = incumbent.get("trialId")
                if not isinstance(trial_id, str) or not trial_id:
                    raise ExperimentDesignError(
                        f"{algorithm} seed {seed} incumbent has no trialId"
                    )
                source_candidate = (
                    run_directory / "trials" / trial_id / "candidate.json"
                )
                if not source_candidate.is_file():
                    raise ExperimentDesignError(
                        f"{algorithm} seed {seed} incumbent candidate is missing"
                    )
                candidate = load_json(source_candidate)
                cost = _valid_development_cost(incumbent.get("cost"))
                coverage = {
                    "expectedProfileCount": len(expected_profile_ids),
                    "observedProfileCount": len(expected_profile_ids),
                    "successfulProfileCount": len(expected_profile_ids),
                }
                qualification_reason = "FULL_DEVELOPMENT_WORKLOAD_SUCCESS"

        qualified = qualification_reason == "FULL_DEVELOPMENT_WORKLOAD_SUCCESS"
        candidate_id = candidate.get("candidateId") if candidate is not None else None
        finalist_id = f"{algorithm}-seed{seed}-v0" if qualified else None
        slot_results.append(
            {
                "algorithm": algorithm,
                "seed": seed,
                "status": QUALIFIED if qualified else NO_FEASIBLE_CANDIDATE,
                "reason": qualification_reason,
                "finalistId": finalist_id,
                "candidateId": candidate_id,
                "developmentCost": cost,
                "developmentCoverage": coverage,
                "sourceRun": source_run,
            }
        )
        if not qualified:
            continue
        assert candidate is not None and cost is not None and finalist_id is not None
        candidate_id = candidate.get("candidateId")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ExperimentDesignError(f"{algorithm} seed {seed} candidate has no ID")
        output_path = output_directory / f"{algorithm}-seed{seed}-candidate-v0.json"
        _write_or_verify(output_path, candidate, "finalist candidate")
        finalists.append(
            {
                "finalistId": finalist_id,
                "algorithm": algorithm,
                "seed": seed,
                "candidateId": candidate_id,
                "developmentCost": float(cost),
                "candidate": _binding(output_path, output_directory),
                "sourceRun": source_run,
            }
        )
    index = {
        "schemaVersion": FINALIST_INDEX_SCHEMA_VERSION,
        "campaignId": campaign["campaignId"],
        "qualificationPolicy": QUALIFICATION_POLICY,
        "slotResults": slot_results,
        "finalists": finalists,
    }
    _validate_finalist_index(index, workspace=workspace)
    write_json(output_directory / "index-v0.json", index)
    return index


def _valid_development_cost(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ExperimentDesignError("run incumbent has invalid development cost")
    return float(value)


def _development_profile_ids(manifest: Mapping[str, Any]) -> set[str]:
    try:
        profiles = manifest["experiment"]["evaluator"]["workload"]["profiles"]
    except (KeyError, TypeError) as failure:
        raise ExperimentDesignError("run manifest has no development workload") from failure
    if not isinstance(profiles, list) or not profiles:
        raise ExperimentDesignError("run development workload profiles are invalid")
    profile_ids = {
        profile.get("profileId") for profile in profiles if isinstance(profile, dict)
    }
    if (
        None in profile_ids
        or len(profile_ids) != len(profiles)
    ):
        raise ExperimentDesignError("run development workload profiles are invalid")
    return profile_ids


def _load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as failure:
                raise ExperimentDesignError(
                    f"invalid event JSON at {path}:{line_number}"
                ) from failure
            if not isinstance(event, dict):
                raise ExperimentDesignError("search event must be an object")
            events.append(event)
    if [event.get("sequence") for event in events] != list(range(len(events))):
        raise ExperimentDesignError("search event sequence is broken")
    return events


def _smac_candidate_coverage(
    events_path: Path,
    *,
    candidate_id: Any,
    expected_profile_ids: set[str],
) -> dict[str, int]:
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ExperimentDesignError("SMAC incumbent candidate has no ID")
    observations: dict[str, list[Mapping[str, Any]]] = {}
    for event in _load_events(events_path):
        if event.get("type") != "TRIAL_FINISHED":
            continue
        payload = event.get("payload")
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or result.get("candidate_id") != candidate_id:
            continue
        profile_id = result.get("instance_id")
        if profile_id not in expected_profile_ids:
            raise ExperimentDesignError(
                "SMAC incumbent observation references an unknown profile"
            )
        observations.setdefault(profile_id, []).append(result)
    successful = {
        profile_id
        for profile_id, results in observations.items()
        if results
        and all(
            result.get("status") == "SUCCESS"
            and result.get("correctness") == "CORRECT"
            for result in results
        )
    }
    return {
        "expectedProfileCount": len(expected_profile_ids),
        "observedProfileCount": len(observations),
        "successfulProfileCount": len(successful),
    }


def _validate_finalist_index(
    index: Mapping[str, Any], *, workspace: Mapping[str, Any]
) -> None:
    expected_fields = {
        "schemaVersion",
        "campaignId",
        "qualificationPolicy",
        "slotResults",
        "finalists",
    }
    if set(index) != expected_fields or index.get("schemaVersion") != FINALIST_INDEX_SCHEMA_VERSION:
        raise ExperimentDesignError("finalist index fields do not match v0")
    if index.get("qualificationPolicy") != QUALIFICATION_POLICY:
        raise ExperimentDesignError("unsupported finalist qualification policy")
    slots = index.get("slotResults")
    finalists = index.get("finalists")
    if not isinstance(slots, list) or not isinstance(finalists, list):
        raise ExperimentDesignError("finalist index arrays are invalid")
    expected_coordinates = {
        (slot["algorithm"], slot["seed"]) for slot in workspace["runSlots"]
    }
    actual_coordinates = {(slot.get("algorithm"), slot.get("seed")) for slot in slots}
    if len(actual_coordinates) != len(slots) or actual_coordinates != expected_coordinates:
        raise ExperimentDesignError("finalist slot results differ from campaign slots")
    finalist_ids = {item.get("finalistId") for item in finalists}
    if None in finalist_ids or len(finalist_ids) != len(finalists):
        raise ExperimentDesignError("finalist IDs must be unique non-empty strings")
    qualified_ids = {
        slot.get("finalistId")
        for slot in slots
        if slot.get("status") == QUALIFIED
    }
    if qualified_ids != finalist_ids:
        raise ExperimentDesignError("qualified slots and finalists disagree")
    for slot in slots:
        if slot.get("status") not in {QUALIFIED, NO_FEASIBLE_CANDIDATE}:
            raise ExperimentDesignError("finalist slot has an invalid status")
        coverage = slot.get("developmentCoverage")
        if not isinstance(coverage, dict) or set(coverage) != {
            "expectedProfileCount",
            "observedProfileCount",
            "successfulProfileCount",
        }:
            raise ExperimentDesignError("finalist slot coverage is invalid")
        counts = list(coverage.values())
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise ExperimentDesignError("finalist slot coverage count is invalid")
        if not 0 <= coverage["successfulProfileCount"] <= coverage["observedProfileCount"] <= coverage["expectedProfileCount"]:
            raise ExperimentDesignError("finalist slot coverage counts are inconsistent")


def prepare_confirmation(workspace_directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind every seed finalist to one common sealed-test schedule."""

    root = workspace_directory.resolve()
    _, campaign, corpus = load_campaign_workspace(root)
    finalists_directory = root / "confirmation" / "finalists"
    index_path = finalists_directory / "index-v0.json"
    if not index_path.is_file():
        raise ExperimentDesignError("collect finalists before preparing confirmation")
    index = load_json(index_path)
    if (
        index.get("schemaVersion") != FINALIST_INDEX_SCHEMA_VERSION
        or index.get("campaignId") != campaign["campaignId"]
    ):
        raise ExperimentDesignError("finalist index belongs to another campaign")
    _validate_finalist_index(index, workspace=load_json(root / "workspace-v0.json"))
    finalists = index.get("finalists")
    if not isinstance(finalists, list) or not finalists:
        raise ExperimentDesignError("finalist index is empty")
    finalist_ids = [item.get("finalistId") for item in finalists]
    if any(not isinstance(value, str) or not value for value in finalist_ids) or (
        len(set(finalist_ids)) != len(finalist_ids)
    ):
        raise ExperimentDesignError("finalist IDs must be non-empty and unique")
    test_groups = [
        group["groupId"] for group in corpus["groups"] if group["partition"] == "test"
    ]
    schedule = build_confirmation_schedule(
        campaign_id=campaign["campaignId"],
        default_candidate_id=campaign["defaultCandidateId"],
        workload_group_ids=test_groups,
        candidate_ids=finalist_ids,
        repetitions=campaign["confirmation"]["repetitions"],
        seed=campaign["confirmation"]["scheduleSeed"],
    )
    confirmation_directory = root / "confirmation"
    schedule_path = confirmation_directory / "schedule-v0.json"
    _write_or_verify(schedule_path, schedule, "confirmation schedule")
    validate_confirmation_schedule(schedule)

    candidate_entries = []
    for finalist in finalists:
        candidate_binding = finalist.get("candidate")
        candidate_path = _validate_binding(
            candidate_binding, finalists_directory, "finalist candidate"
        )
        candidate = load_json(candidate_path)
        if candidate.get("candidateId") != finalist.get("candidateId"):
            raise ExperimentDesignError("finalist candidateId binding mismatch")
        candidate_entries.append(
            {
                "finalistId": finalist["finalistId"],
                "algorithm": finalist["algorithm"],
                "seed": finalist["seed"],
                "candidateId": finalist["candidateId"],
                "path": _relative(candidate_path, confirmation_directory),
                "sha256": sha256_file(candidate_path),
            }
        )
    candidate_index = {
        "schemaVersion": CANDIDATE_INDEX_SCHEMA_VERSION,
        "scheduleSha256": schedule["scheduleSha256"],
        "candidates": candidate_entries,
    }
    candidate_index_path = confirmation_directory / "candidate-index-v0.json"
    _write_or_verify(candidate_index_path, candidate_index, "confirmation candidate index")
    return schedule, candidate_index


def _validate_run_identity(
    manifest: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    algorithm: str,
    seed: int,
    campaign: Mapping[str, Any] | None = None,
    development_baseline_digests: set[str] | None = None,
) -> None:
    expected_state_schema = (
        "cvc5-smac-search-state-v0" if algorithm == "smac" else "cvc5-search-state-v0"
    )
    if state.get("schemaVersion") != expected_state_schema or state.get("status") != "FINISHED":
        raise ExperimentDesignError(f"{algorithm} seed {seed} run is not finished")
    experiment = manifest.get("experiment")
    strategy = experiment.get("strategy") if isinstance(experiment, dict) else None
    expected_names = {
        "random": "random-search-v0",
        "evolution": "evolution-search-v0",
        "smac": "smac3-algorithm-configuration-v0",
    }
    if (
        not isinstance(strategy, dict)
        or strategy.get("name") != expected_names[algorithm]
        or strategy.get("seed") != seed
    ):
        raise ExperimentDesignError(f"{algorithm} seed {seed} manifest mismatch")
    if campaign is None:
        return
    budget = experiment.get("budget") if isinstance(experiment, dict) else None
    evaluator = experiment.get("evaluator") if isinstance(experiment, dict) else None
    if not isinstance(budget, dict) or not isinstance(evaluator, dict):
        raise ExperimentDesignError(f"{algorithm} seed {seed} has no frozen inputs")
    campaign_budget = campaign["budget"]
    evaluation_key = "maxTrials" if algorithm == "smac" else "maxEvaluations"
    expected_budget = {
        "maxWallTimeSeconds": float(campaign_budget["wallClockSeconds"]),
        evaluation_key: campaign_budget["maximumEvaluations"],
        "perTrialTimeoutSeconds": float(campaign_budget["perTrialTimeoutSeconds"]),
    }
    actual_budget = {key: budget.get(key) for key in expected_budget}
    if actual_budget != expected_budget:
        raise ExperimentDesignError(
            f"{algorithm} seed {seed} budget differs from the campaign: "
            f"expected={expected_budget}, actual={actual_budget}"
        )
    if development_baseline_digests is not None and evaluator.get(
        "baselineManifestSha256"
    ) not in development_baseline_digests:
        raise ExperimentDesignError(
            f"{algorithm} seed {seed} was not evaluated on a development workload"
        )
    scoring_policy = evaluator.get("scoringPolicy")
    if (
        not isinstance(scoring_policy, dict)
        or scoring_policy.get("aggregation") != campaign["objective"]
    ):
        raise ExperimentDesignError(
            f"{algorithm} seed {seed} used another campaign objective"
        )


def extract_anytime_curve(
    run_directory: Path, *, algorithm: str, seed: int
) -> dict[str, Any]:
    """Normalize only qualified full-workload incumbents.

    SMAC's intermediate per-instance costs are not comparable with the
    whole-workload costs emitted by Random Search and ES.  A SMAC point enters
    the curve only after one candidate has successfully covered every
    development profile.  Runs without such a point retain the default
    fallback speedup of 1.0 and explicitly report no qualified incumbent.
    """

    manifest = load_json(run_directory / "manifest.json")
    state = load_json(run_directory / "state.json")
    _validate_run_identity(manifest, state, algorithm=algorithm, seed=seed)
    events = _load_events(run_directory / "events.jsonl")
    if not events or events[0].get("type") != "EXPERIMENT_CREATED":
        raise ExperimentDesignError(f"{algorithm} seed {seed} has no creation event")
    if [event.get("sequence") for event in events] != list(range(len(events))):
        raise ExperimentDesignError(f"{algorithm} seed {seed} event sequence is broken")
    started_millis = events[0].get("atEpochMillis")
    if not isinstance(started_millis, int):
        raise ExperimentDesignError("search creation timestamp is invalid")
    points = (
        _smac_anytime_points(
            events,
            started_millis=started_millis,
            expected_profile_ids=_development_profile_ids(manifest),
        )
        if algorithm == "smac"
        else _ordinary_anytime_points(events, started_millis=started_millis)
    )
    active_nanos = state.get("activeWallTimeNanos")
    if isinstance(active_nanos, bool) or not isinstance(active_nanos, int) or active_nanos <= 0:
        raise ExperimentDesignError(f"{algorithm} seed {seed} has invalid active time")
    duration_seconds = active_nanos / 1e9
    if points:
        duration_seconds = max(duration_seconds, points[-1]["elapsedSeconds"])
    final_cost = float(points[-1]["cost"]) if points else None
    return {
        "algorithm": algorithm,
        "seed": seed,
        "durationSeconds": duration_seconds,
        "completedEvaluationCount": state.get("completedTrialCount"),
        "hasQualifiedIncumbent": final_cost is not None,
        "finalCost": final_cost,
        "finalGeometricSpeedup": 1.0 / final_cost if final_cost is not None else 1.0,
        "normalizedAreaUnderSpeedupCurve": _anytime_area(points, duration_seconds),
        "points": points,
    }


def _ordinary_anytime_points(
    events: Sequence[Mapping[str, Any]], *, started_millis: int
) -> list[dict[str, Any]]:
    finished_evaluations = 0
    points = []
    for event in events[1:]:
        if event.get("type") == "TRIAL_FINISHED":
            finished_evaluations += 1
        if event.get("type") != "INCUMBENT_CHANGED":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise ExperimentDesignError("incumbent event payload is invalid")
        cost = payload.get("cost")
        at_millis = event.get("atEpochMillis")
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(float(cost))
            or float(cost) <= 0
            or not isinstance(at_millis, int)
        ):
            raise ExperimentDesignError("incumbent event has invalid cost or time")
        points.append(
            {
                "elapsedSeconds": max(0.0, (at_millis - started_millis) / 1000.0),
                "evaluationCount": finished_evaluations,
                "candidateId": payload.get("candidateId"),
                "cost": float(cost),
                "geometricSpeedup": 1.0 / float(cost),
            }
        )
    return points


def _smac_anytime_points(
    events: Sequence[Mapping[str, Any]],
    *,
    started_millis: int,
    expected_profile_ids: set[str],
) -> list[dict[str, Any]]:
    observations: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    qualified_candidates: set[str] = set()
    finished_evaluations = 0
    incumbent_cost = math.inf
    points: list[dict[str, Any]] = []
    for event in events[1:]:
        if event.get("type") != "TRIAL_FINISHED":
            continue
        finished_evaluations += 1
        payload = event.get("payload")
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ExperimentDesignError("SMAC trial event has no result")
        candidate_id = result.get("candidate_id")
        profile_id = result.get("instance_id")
        if not isinstance(candidate_id, str) or profile_id not in expected_profile_ids:
            raise ExperimentDesignError("SMAC trial result has invalid coordinates")
        observations.setdefault(candidate_id, {}).setdefault(profile_id, []).append(
            result
        )
        candidate_results = observations[candidate_id]
        if candidate_id in qualified_candidates or set(candidate_results) != expected_profile_ids:
            continue
        if not all(
            profile_results
            and all(
                item.get("status") == "SUCCESS"
                and item.get("correctness") == "CORRECT"
                and isinstance(item.get("cost"), (int, float))
                and not isinstance(item.get("cost"), bool)
                and math.isfinite(float(item["cost"]))
                and float(item["cost"]) > 0
                for item in profile_results
            )
            for profile_results in candidate_results.values()
        ):
            continue
        qualified_candidates.add(candidate_id)
        cost = statistics.fmean(
            statistics.fmean(
                float(item["cost"]) for item in candidate_results[profile_id]
            )
            for profile_id in expected_profile_ids
        )
        if cost >= incumbent_cost:
            continue
        incumbent_cost = cost
        at_millis = event.get("atEpochMillis")
        if not isinstance(at_millis, int):
            raise ExperimentDesignError("SMAC trial timestamp is invalid")
        points.append(
            {
                "elapsedSeconds": max(0.0, (at_millis - started_millis) / 1000.0),
                "evaluationCount": finished_evaluations,
                "candidateId": candidate_id,
                "cost": cost,
                "geometricSpeedup": 1.0 / cost,
            }
        )
    return points


def _anytime_area(points: Sequence[Mapping[str, Any]], duration: float) -> float:
    if duration <= 0:
        raise ExperimentDesignError("anytime duration must be positive")
    area = 0.0
    previous_time = 0.0
    previous_speedup = 1.0
    for point in points:
        current_time = min(float(point["elapsedSeconds"]), duration)
        area += max(0.0, current_time - previous_time) * previous_speedup
        previous_time = current_time
        previous_speedup = float(point["geometricSpeedup"])
    area += max(0.0, duration - previous_time) * previous_speedup
    return area / duration


def summarize_anytime(
    workspace_directory: Path,
    *,
    time_fractions: Sequence[float] = DEFAULT_TIME_FRACTIONS,
) -> dict[str, Any]:
    root = workspace_directory.resolve()
    workspace, campaign, _ = load_campaign_workspace(root)
    fractions = [float(value) for value in time_fractions]
    if (
        len(fractions) < 2
        or fractions != sorted(set(fractions))
        or fractions[0] != 0.0
        or fractions[-1] != 1.0
    ):
        raise ExperimentDesignError(
            "time fractions must be unique, sorted, and span exactly 0 to 1"
        )
    algorithms = []
    for algorithm in ALGORITHMS:
        slots = [slot for slot in workspace["runSlots"] if slot["algorithm"] == algorithm]
        runs = [
            extract_anytime_curve(
                _resolve(root, slot["path"]), algorithm=algorithm, seed=slot["seed"]
            )
            for slot in slots
        ]
        aggregate = []
        for fraction in fractions:
            values = [
                _speedup_at(run["points"], run["durationSeconds"] * fraction)
                for run in runs
            ]
            aggregate.append(
                {
                    "timeFraction": fraction,
                    "medianGeometricSpeedup": statistics.median(values),
                    "lowerQuartileGeometricSpeedup": _quantile(values, 0.25),
                    "upperQuartileGeometricSpeedup": _quantile(values, 0.75),
                }
            )
        algorithms.append(
            {
                "algorithm": algorithm,
                "seedCount": len(runs),
                "runs": runs,
                "aggregate": aggregate,
            }
        )
    summary = {
        "schemaVersion": ANYTIME_SUMMARY_SCHEMA_VERSION,
        "campaignId": campaign["campaignId"],
        "timeFractions": fractions,
        "algorithms": algorithms,
    }
    write_json(root / "reports" / "anytime-summary-v0.json", summary)
    return summary


def _speedup_at(points: Sequence[Mapping[str, Any]], elapsed: float) -> float:
    speedup = 1.0
    for point in points:
        if float(point["elapsedSeconds"]) > elapsed:
            break
        speedup = float(point["geometricSpeedup"])
    return speedup


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("initialize")
    initialize.add_argument("--campaign", type=Path, required=True)
    initialize.add_argument("--output-dir", type=Path, required=True)
    finalists = commands.add_parser("collect-finalists")
    finalists.add_argument("--workspace", type=Path, required=True)
    prepare = commands.add_parser("prepare-confirmation")
    prepare.add_argument("--workspace", type=Path, required=True)
    anytime = commands.add_parser("summarize-anytime")
    anytime.add_argument("--workspace", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        if arguments.command == "initialize":
            workspace = initialize_campaign_workspace(
                arguments.campaign, arguments.output_dir
            )
            print(
                f"initialized {workspace['campaignId']} with "
                f"{len(workspace['runSlots'])} run slots"
            )
        elif arguments.command == "collect-finalists":
            index = collect_finalists(arguments.workspace)
            print(f"collected {len(index['finalists'])} seed finalists")
        elif arguments.command == "prepare-confirmation":
            schedule, candidate_index = prepare_confirmation(arguments.workspace)
            print(
                f"prepared {len(candidate_index['candidates'])} finalists and "
                f"{len(schedule['trials'])} confirmation trials"
            )
        else:
            summary = summarize_anytime(arguments.workspace)
            print(
                f"wrote anytime summary for {len(summary['algorithms'])} algorithms"
            )
        return 0
    except (OSError, ValueError) as failure:
        print(f"multi-seed campaign failed: {failure}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
