"""Durable SMAC3 search over real per-profile cvc5 replay trials.

The run has two persistence domains: SMAC's run history/intensifier files and
the external evaluator checkpoint.  This module joins them with immutable,
checksummed SMAC generations.  The external state points only to a complete
generation, so a crash between ``ask``, real JVM execution, ``tell``, and disk
flush can always be reconciled without inventing an observation.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import signal
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from ksmt_ml.evaluator import (
    EvaluatorError,
    ExperimentInterrupted,
    InvalidReplayReportError,
    ReplayProcessError,
    ReplayTimeoutError,
    canonical_json,
    describe_workload,
    load_json,
    sha256_file,
    sha256_json,
    validate_baseline_manifest,
    validate_report,
)
from ksmt_ml.search import (
    PROCESS_HEARTBEAT_INTERVAL_SECONDS,
    SEARCH_EVALUATOR_VERSION,
    CandidateDisqualifiedError,
    FileEvaluationCache,
    RealCvc5ProfileTrialEvaluator,
    SearchError,
    TrialRequest,
    TrialResult,
    _claim_real_evaluation_number,
    _failed_trial_result,
    _is_search_sha256,
    _fsync_directory,
    _search_non_negative_int,
    _trial_request_from_pending,
    _validate_pending_trial,
    _validate_search_parameter_space,
    _validate_trial_result,
    _write_trial_failure,
    atomic_write_json,
)
from ksmt_ml.smac_adapter import (
    SMAC_ADAPTER_VERSION,
    SMAC_REQUIRED_VERSION,
    SmacAdapterError,
    SmacAskTellStrategy,
)


SMAC_MANIFEST_SCHEMA_VERSION = "cvc5-smac-search-manifest-v0"
SMAC_STATE_SCHEMA_VERSION = "cvc5-smac-search-state-v0"
SMAC_EVENT_SCHEMA_VERSION = "cvc5-smac-search-event-v0"
SMAC_CHECKPOINT_SCHEMA_VERSION = "cvc5-smac-native-checkpoint-v0"
SMAC_SCENARIO_NAME = "ksmt-cvc5-smac-v0"
SMAC_NATIVE_FILES = (
    "scenario.json",
    "configspace.json",
    "optimization.json",
    "runhistory.json",
    "intensifier.json",
)
SMAC_EVENT_TYPES = frozenset(
    {
        "EXPERIMENT_CREATED",
        "EXPERIMENT_RESUMED",
        "TRIAL_PROPOSED",
        "TRIAL_STARTED",
        "TRIAL_RECOVERED",
        "CACHE_HIT",
        "UNCLEAN_TRIAL_RECOVERED",
        "TRIAL_FINISHED",
        "INCUMBENT_CHANGED",
        "EXPERIMENT_INTERRUPTED",
        "EXPERIMENT_FINISHED",
    }
)


class SmacSearchError(SearchError):
    """The durable SMAC experiment is inconsistent or corrupt."""


class SmacRunStore:
    """Atomic external state and append-only trace for one serial SMAC run."""

    def __init__(self, run_directory: Path):
        self.run_directory = run_directory.resolve()
        self.manifest_path = self.run_directory / "manifest.json"
        self.state_path = self.run_directory / "state.json"
        self.events_path = self.run_directory / "events.jsonl"
        self.trials_directory = self.run_directory / "trials"
        self._next_event_sequence = 0

    def open_or_create(
        self, experiment: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        _validate_smac_experiment(experiment)
        if self.manifest_path.exists():
            manifest = load_json(self.manifest_path)
            self._validate_manifest(manifest)
            if manifest["experiment"] != experiment:
                raise SmacSearchError(
                    "existing SMAC manifest differs from the requested experiment"
                )
            if not self.state_path.is_file():
                raise SmacSearchError("existing SMAC run has no external checkpoint")
            state = load_json(self.state_path)
            self._validate_state(state, manifest["runId"])
            self._next_event_sequence = _repair_smac_event_log(self.events_path)
            return manifest, state, False

        if self.run_directory.exists() and any(self.run_directory.iterdir()):
            raise SmacSearchError("new SMAC run directory is not empty")
        self.trials_directory.mkdir(parents=True, exist_ok=True)
        run_id = f"cvc5-smac-search-v0-{uuid.uuid4()}"
        manifest = {
            "schemaVersion": SMAC_MANIFEST_SCHEMA_VERSION,
            "runId": run_id,
            "createdAtEpochMillis": int(time.time() * 1000),
            "experimentSha256": sha256_json(experiment),
            "experiment": dict(experiment),
        }
        state = {
            "schemaVersion": SMAC_STATE_SCHEMA_VERSION,
            "runId": run_id,
            "status": "RUNNING",
            "activeWallTimeNanos": 0,
            "actualEvaluationCount": 0,
            "completedTrialCount": 0,
            "cacheHitCount": 0,
            "nextProposalIndex": 0,
            "completedRequestIds": [],
            "pendingTrial": None,
            "incumbent": None,
            "smacCheckpoint": None,
            "stopReason": None,
            "updatedAtEpochMillis": int(time.time() * 1000),
        }
        atomic_write_json(self.manifest_path, manifest)
        self.save_state(state)
        return manifest, state, True

    def append_event(self, event_type: str, **payload: Any) -> None:
        event = {
            "schemaVersion": SMAC_EVENT_SCHEMA_VERSION,
            "sequence": self._next_event_sequence,
            "atEpochMillis": int(time.time() * 1000),
            "type": event_type,
            "payload": payload,
        }
        _validate_smac_event(event, self._next_event_sequence)
        with self.events_path.open("a", encoding="utf-8") as destination:
            destination.write(canonical_json(event).decode("utf-8") + "\n")
            destination.flush()
            os.fsync(destination.fileno())
        self._next_event_sequence += 1

    def save_state(self, state: Mapping[str, Any]) -> None:
        checkpoint = dict(state)
        checkpoint["updatedAtEpochMillis"] = int(time.time() * 1000)
        self._validate_state(checkpoint, checkpoint.get("runId"))
        atomic_write_json(self.state_path, checkpoint)

    def trial_directory(self, trial_id: str) -> Path:
        directory = self.trials_directory / trial_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _validate_manifest(manifest: Any) -> None:
        required = {
            "schemaVersion",
            "runId",
            "createdAtEpochMillis",
            "experimentSha256",
            "experiment",
        }
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise SmacSearchError("SMAC manifest fields do not match v0")
        if manifest["schemaVersion"] != SMAC_MANIFEST_SCHEMA_VERSION:
            raise SmacSearchError("unsupported SMAC manifest schema")
        if not isinstance(manifest["runId"], str) or not manifest["runId"]:
            raise SmacSearchError("SMAC manifest has invalid runId")
        _search_non_negative_int(manifest, "createdAtEpochMillis")
        _validate_smac_experiment(manifest["experiment"])
        if manifest["experimentSha256"] != sha256_json(manifest["experiment"]):
            raise SmacSearchError("SMAC manifest experiment digest mismatch")

    @staticmethod
    def _validate_state(state: Any, run_id: Any) -> None:
        required = {
            "schemaVersion",
            "runId",
            "status",
            "activeWallTimeNanos",
            "actualEvaluationCount",
            "completedTrialCount",
            "cacheHitCount",
            "nextProposalIndex",
            "completedRequestIds",
            "pendingTrial",
            "incumbent",
            "smacCheckpoint",
            "stopReason",
            "updatedAtEpochMillis",
        }
        if not isinstance(state, dict) or set(state) != required:
            raise SmacSearchError("SMAC state fields do not match v0")
        if state["schemaVersion"] != SMAC_STATE_SCHEMA_VERSION:
            raise SmacSearchError("unsupported SMAC state schema")
        if not isinstance(run_id, str) or state["runId"] != run_id:
            raise SmacSearchError("SMAC state belongs to another run")
        if state["status"] not in {"RUNNING", "INTERRUPTED", "FINISHED"}:
            raise SmacSearchError("SMAC state has invalid status")
        for field in (
            "activeWallTimeNanos",
            "actualEvaluationCount",
            "completedTrialCount",
            "cacheHitCount",
            "nextProposalIndex",
            "updatedAtEpochMillis",
        ):
            _search_non_negative_int(state, field)
        completed = state["completedRequestIds"]
        if (
            not isinstance(completed, list)
            or any(not isinstance(value, str) or not value for value in completed)
            or len(completed) != len(set(completed))
        ):
            raise SmacSearchError("completedRequestIds must be unique strings")
        if state["completedTrialCount"] != len(completed):
            raise SmacSearchError("completed SMAC trial count is inconsistent")
        expected_proposals = state["completedTrialCount"] + (
            1 if state["pendingTrial"] is not None else 0
        )
        if state["nextProposalIndex"] != expected_proposals:
            raise SmacSearchError("SMAC proposal index is inconsistent")
        if state["cacheHitCount"] > state["completedTrialCount"]:
            raise SmacSearchError("SMAC cache hits exceed completed trials")
        if state["actualEvaluationCount"] > state["completedTrialCount"] + (
            1 if state["pendingTrial"] is not None else 0
        ):
            raise SmacSearchError("SMAC real evaluations exceed known trials")
        pending = state["pendingTrial"]
        if pending is not None:
            _validate_pending_trial(pending)
            if pending["requestId"] in completed:
                raise SmacSearchError("pending SMAC request is already completed")
        if state["stopReason"] is not None and not isinstance(state["stopReason"], str):
            raise SmacSearchError("SMAC stop reason must be a string or null")
        _validate_incumbent(state["incumbent"])
        checkpoint = state["smacCheckpoint"]
        if checkpoint is not None:
            _validate_checkpoint_descriptor(checkpoint)
            expected_running = 1 if pending is not None else 0
            stats = checkpoint["runhistoryStats"]
            if (
                stats["finished"] != state["completedTrialCount"]
                or stats["running"] != expected_running
                or stats["submitted"] != state["completedTrialCount"] + expected_running
            ):
                raise SmacSearchError(
                    "SMAC native checkpoint disagrees with the external state"
                )
        elif state["completedTrialCount"] or state["pendingTrial"] is not None:
            raise SmacSearchError("SMAC trials exist without a native checkpoint")


class SmacNativeCheckpointStore:
    """Immutable complete generations of SMAC's five native JSON files."""

    def __init__(self, run_directory: Path):
        self.directory = run_directory.resolve() / "smac-checkpoints"

    def capture(self, strategy: SmacAskTellStrategy) -> dict[str, Any]:
        strategy.save_native_state()
        source = Path(strategy.facade.scenario.output_directory)
        generation = self._next_generation()
        destination = self.directory / f"generation-{generation:08d}"
        destination.mkdir(parents=True, exist_ok=False)
        digests: dict[str, str] = {}
        for name in SMAC_NATIVE_FILES:
            source_path = source / name
            if not source_path.is_file():
                raise SmacSearchError(f"SMAC native state is missing {name}")
            # Parsing before copying rejects partially written SMAC files.
            _load_native_json(source_path)
            target = destination / name
            _atomic_copy(source_path, target)
            digests[name] = sha256_file(target)
        runhistory = load_json(destination / "runhistory.json")
        stats = _validate_native_runhistory(runhistory)
        descriptor = {
            "schemaVersion": SMAC_CHECKPOINT_SCHEMA_VERSION,
            "generation": generation,
            "directory": destination.name,
            "files": digests,
            "runhistoryStats": stats,
        }
        atomic_write_json(destination / "complete.json", descriptor)
        return descriptor

    def restore(self, descriptor: Mapping[str, Any], working_directory: Path) -> None:
        self.validate(descriptor)
        source = self.directory / descriptor["directory"]
        working_directory.mkdir(parents=True, exist_ok=True)
        for name in SMAC_NATIVE_FILES:
            path = source / name
            _atomic_copy(path, working_directory / name)
        stats = _validate_native_runhistory(load_json(working_directory / "runhistory.json"))
        if stats != descriptor["runhistoryStats"]:
            raise SmacSearchError("SMAC restored runhistory stats mismatch")

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        _validate_checkpoint_descriptor(descriptor)
        source = self.directory / descriptor["directory"]
        marker_path = source / "complete.json"
        if not marker_path.is_file() or load_json(marker_path) != descriptor:
            raise SmacSearchError("SMAC checkpoint completion marker mismatch")
        for name in SMAC_NATIVE_FILES:
            path = source / name
            if not path.is_file() or sha256_file(path) != descriptor["files"][name]:
                raise SmacSearchError(f"SMAC checkpoint digest mismatch: {name}")
            _load_native_json(path)
        stats = _validate_native_runhistory(load_json(source / "runhistory.json"))
        if stats != descriptor["runhistoryStats"]:
            raise SmacSearchError("SMAC checkpoint runhistory stats mismatch")

    def _next_generation(self) -> int:
        if not self.directory.is_dir():
            return 0
        generations = []
        for path in self.directory.glob("generation-*"):
            try:
                generations.append(int(path.name.removeprefix("generation-")))
            except ValueError:
                continue
        return max(generations, default=-1) + 1


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
            while chunk := input_stream.read(1024 * 1024):
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_native_runhistory(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {
        "stats",
        "data",
        "configs",
        "config_origins",
    }:
        raise SmacSearchError("SMAC runhistory fields do not match 2.4.1")
    stats = value["stats"]
    if not isinstance(stats, dict) or set(stats) != {"submitted", "finished", "running"}:
        raise SmacSearchError("SMAC runhistory stats are invalid")
    normalized: dict[str, int] = {}
    for field in ("submitted", "finished", "running"):
        count = stats[field]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise SmacSearchError(f"SMAC runhistory has invalid {field}")
        normalized[field] = count
    if normalized["submitted"] != normalized["finished"] + normalized["running"]:
        raise SmacSearchError("SMAC runhistory counters are inconsistent")
    if not isinstance(value["data"], list) or len(value["data"]) != normalized["submitted"]:
        raise SmacSearchError("SMAC runhistory data count is inconsistent")
    if normalized["running"] > 1:
        raise SmacSearchError("serial SMAC checkpoint contains multiple running trials")
    return normalized


def _load_native_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as failure:
        raise SmacSearchError(f"invalid SMAC native JSON {path.name}: {failure}") from failure


def _validate_checkpoint_descriptor(value: Any) -> None:
    required = {
        "schemaVersion",
        "generation",
        "directory",
        "files",
        "runhistoryStats",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise SmacSearchError("SMAC checkpoint descriptor fields do not match v0")
    if value["schemaVersion"] != SMAC_CHECKPOINT_SCHEMA_VERSION:
        raise SmacSearchError("unsupported SMAC checkpoint schema")
    _search_non_negative_int(value, "generation")
    expected_directory = f"generation-{value['generation']:08d}"
    if value["directory"] != expected_directory:
        raise SmacSearchError("SMAC checkpoint directory does not match generation")
    files = value["files"]
    if not isinstance(files, dict) or set(files) != set(SMAC_NATIVE_FILES):
        raise SmacSearchError("SMAC checkpoint file inventory is invalid")
    if any(not _is_search_sha256(digest) for digest in files.values()):
        raise SmacSearchError("SMAC checkpoint has an invalid file digest")
    stats = value["runhistoryStats"]
    if not isinstance(stats, dict) or set(stats) != {"submitted", "finished", "running"}:
        raise SmacSearchError("SMAC checkpoint runhistory stats are invalid")
    for field in stats:
        _search_non_negative_int(stats, field)
    if stats["submitted"] != stats["finished"] + stats["running"]:
        raise SmacSearchError("SMAC checkpoint runhistory counters are inconsistent")


def _validate_incumbent(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {"candidate", "cost"}:
        raise SmacSearchError("SMAC incumbent fields do not match v0")
    candidate = value["candidate"]
    if not isinstance(candidate, dict):
        raise SmacSearchError("SMAC incumbent candidate is invalid")
    try:
        from ksmt_ml.evaluator import normalize_candidate

        normalize_candidate(candidate)
    except EvaluatorError as failure:
        raise SmacSearchError(f"SMAC incumbent candidate is invalid: {failure}") from failure
    cost = value["cost"]
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost))
        or cost < 0
    ):
        raise SmacSearchError("SMAC incumbent cost is invalid")


def _validate_smac_event(event: Any, expected_sequence: int) -> None:
    required = {"schemaVersion", "sequence", "atEpochMillis", "type", "payload"}
    if not isinstance(event, dict) or set(event) != required:
        raise SmacSearchError("SMAC event fields do not match v0")
    if event["schemaVersion"] != SMAC_EVENT_SCHEMA_VERSION:
        raise SmacSearchError("unsupported SMAC event schema")
    if event["sequence"] != expected_sequence:
        raise SmacSearchError("SMAC event sequence is not contiguous")
    _search_non_negative_int(event, "atEpochMillis")
    if event["type"] not in SMAC_EVENT_TYPES:
        raise SmacSearchError("SMAC event type is invalid")
    if not isinstance(event["payload"], dict):
        raise SmacSearchError("SMAC event payload must be an object")


def _repair_smac_event_log(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("rb+") as stream:
        lines = stream.readlines()
        valid_count = 0
        valid_bytes = 0
        for index, raw in enumerate(lines):
            if not raw.strip():
                if index == len(lines) - 1:
                    break
                raise SmacSearchError("SMAC event log contains a blank record")
            try:
                event = json.loads(raw.decode("utf-8"))
                _validate_smac_event(event, valid_count)
            except (UnicodeDecodeError, json.JSONDecodeError, SmacSearchError) as failure:
                if index != len(lines) - 1:
                    raise SmacSearchError(
                        f"SMAC event log is corrupt at record {index}: {failure}"
                    ) from failure
                stream.seek(valid_bytes)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                break
            valid_count += 1
            valid_bytes += len(raw)
        return valid_count


def _validate_smac_experiment(experiment: Any) -> None:
    if not isinstance(experiment, dict) or set(experiment) != {
        "strategy",
        "budget",
        "evaluator",
        "objective",
    }:
        raise SmacSearchError("SMAC experiment fields do not match v0")
    strategy = experiment["strategy"]
    if not isinstance(strategy, dict) or set(strategy) != {
        "name",
        "adapterVersion",
        "smacVersion",
        "seed",
        "scenarioName",
        "additionalInitialConfigurations",
        "instanceFeaturePolicy",
        "fidelityPolicy",
    }:
        raise SmacSearchError("SMAC strategy contract is invalid")
    if (
        strategy["name"] != "smac3-algorithm-configuration-v0"
        or strategy["adapterVersion"] != SMAC_ADAPTER_VERSION
        or strategy["smacVersion"] != SMAC_REQUIRED_VERSION
        or strategy["scenarioName"] != SMAC_SCENARIO_NAME
        or strategy["instanceFeaturePolicy"] != "one-hot-identity-v0"
        or strategy["fidelityPolicy"] != "disabled-until-qualified-v0"
    ):
        raise SmacSearchError("SMAC strategy contract is unsupported")
    for field, minimum in (("seed", 0), ("additionalInitialConfigurations", 0)):
        value = strategy[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise SmacSearchError(f"SMAC strategy has invalid {field}")
    budget = experiment["budget"]
    if not isinstance(budget, dict) or set(budget) != {
        "maxTrials",
        "maxWallTimeSeconds",
        "perTrialTimeoutSeconds",
    }:
        raise SmacSearchError("SMAC budget contract is invalid")
    if isinstance(budget["maxTrials"], bool) or not isinstance(budget["maxTrials"], int) or budget[
        "maxTrials"
    ] <= 0:
        raise SmacSearchError("SMAC maxTrials must be positive")
    for field in ("maxWallTimeSeconds", "perTrialTimeoutSeconds"):
        value = budget[field]
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
        ):
            raise SmacSearchError(f"SMAC budget has invalid {field}")
    evaluator = experiment["evaluator"]
    expected_evaluator = {
        "version",
        "tsaCliJar",
        "tsaCliJarSha256",
        "java",
        "host",
        "backendRole",
        "smacRepetitions",
        "warmups",
        "progressIntervalSeconds",
        "parameterSpace",
        "parameterSpaceSha256",
        "baseline",
        "baselineSha256",
        "baselineManifest",
        "baselineManifestSha256",
        "workload",
        "scoringPolicy",
    }
    if not isinstance(evaluator, dict) or set(evaluator) != expected_evaluator:
        raise SmacSearchError("SMAC evaluator contract is invalid")
    if evaluator["version"] != SEARCH_EVALUATOR_VERSION:
        raise SmacSearchError("SMAC evaluator version is unsupported")
    for field in ("tsaCliJar", "java", "backendRole", "parameterSpace", "baseline", "baselineManifest"):
        if not isinstance(evaluator[field], str) or not evaluator[field]:
            raise SmacSearchError(f"SMAC evaluator has invalid {field}")
    for field in (
        "tsaCliJarSha256",
        "parameterSpaceSha256",
        "baselineSha256",
        "baselineManifestSha256",
    ):
        if not _is_search_sha256(evaluator[field]):
            raise SmacSearchError(f"SMAC evaluator has invalid {field}")
    for field, minimum in (("smacRepetitions", 1), ("warmups", 0), ("progressIntervalSeconds", 0)):
        value = evaluator[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise SmacSearchError(f"SMAC evaluator has invalid {field}")
    host = evaluator["host"]
    if not isinstance(host, dict) or set(host) != {"system", "release", "machine", "node"}:
        raise SmacSearchError("SMAC evaluator host identity is invalid")
    if any(not isinstance(value, str) for value in host.values()):
        raise SmacSearchError("SMAC evaluator host identity values are invalid")
    policy = evaluator["scoringPolicy"]
    if not isinstance(policy, dict) or set(policy) != {
        "timeoutPenalty",
        "rewardClip",
        "excludeRecordedFailures",
    }:
        raise SmacSearchError("SMAC scoring policy is invalid")
    for field in ("timeoutPenalty", "rewardClip"):
        value = policy[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
        ):
            raise SmacSearchError(f"SMAC scoring policy has invalid {field}")
    if not isinstance(policy["excludeRecordedFailures"], bool):
        raise SmacSearchError("SMAC recorded-failure policy is invalid")
    from ksmt_ml.evaluator import validate_workload_manifest

    validate_workload_manifest(evaluator["workload"])
    if experiment["objective"] != {
        "name": "smac-instance-mean-normalized-cost-v0",
        "direction": "minimize",
    }:
        raise SmacSearchError("SMAC objective contract is invalid")


def _log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def _load_trial_result(path: Path, request: TrialRequest) -> TrialResult:
    value = load_json(path)
    expected_fields = set(TrialResult.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise SmacSearchError("persisted SMAC trial result fields do not match v0")
    result = TrialResult(**value)
    _validate_trial_result(result)
    expected = {
        "request_id": request.request_id,
        "candidate_id": request.candidate["candidateId"],
        "instance_id": request.instance_id,
        "seed": request.seed,
        "repetition": request.repetition,
        "budget": request.budget,
    }
    for field, expected_value in expected.items():
        if getattr(result, field) != expected_value:
            raise SmacSearchError(f"persisted trial result mismatches {field}")
    return result


def _working_smac_directory(run_directory: Path, seed: int) -> Path:
    return run_directory.resolve() / "smac" / SMAC_SCENARIO_NAME / str(seed)


def _migrate_smac_241_instance_feature_pca(working_directory: Path) -> bool:
    """Repair pre-fix SMAC 2.4.1 metadata after a verified restore.

    The immutable checkpoint remains untouched and checksummed.  Only its
    disposable working copy is updated so that SMAC accepts the explicitly
    configured no-PCA surrogate and can continue from the preserved
    runhistory.  A subsequent checkpoint captures the repaired metadata.
    """

    scenario_path = working_directory / "scenario.json"
    scenario = load_json(scenario_path)
    if not scenario.get("instance_features"):
        return False
    metadata = scenario.get("_meta")
    model = metadata.get("model") if isinstance(metadata, dict) else None
    if not isinstance(model, dict) or model.get("name") != "RandomForest":
        raise SmacSearchError("restored SMAC scenario has unexpected model metadata")
    pca_components = model.get("pca_components")
    if pca_components is None:
        return False
    if pca_components != 4:
        raise SmacSearchError(
            "restored SMAC scenario has an unsupported instance-feature PCA policy"
        )
    model["pca_components"] = None
    atomic_write_json(scenario_path, scenario)
    return True


def _prepare_experiment(arguments: argparse.Namespace) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]
]:
    parameter_space = load_json(arguments.parameter_space)
    _validate_search_parameter_space(parameter_space)
    baseline = load_json(arguments.baseline)
    if not arguments.tsa_cli_jar.is_file():
        raise SmacSearchError(f"TSA CLI jar does not exist: {arguments.tsa_cli_jar}")
    try:
        validate_report(baseline)
        workload = describe_workload(
            profile_directory=arguments.profile_dir,
            profiles=arguments.profile,
            backend_role=arguments.backend_role,
        )
        baseline_manifest = load_json(arguments.baseline_manifest)
        validate_baseline_manifest(
            baseline_manifest,
            report_path=arguments.baseline,
            report=baseline,
            parameter_space_path=arguments.parameter_space,
            parameter_space=parameter_space,
            tsa_cli_jar=arguments.tsa_cli_jar,
            current_workload=workload,
            expected_timeout_penalty=arguments.timeout_penalty,
        )
    except EvaluatorError as failure:
        raise SmacSearchError(f"SMAC baseline preflight failed: {failure}") from failure
    for field, minimum in (
        ("max_trials", 1),
        ("repetitions", 1),
        ("warmups", 0),
        ("progress_interval_seconds", 0),
        ("additional_initial_configurations", 0),
    ):
        value = getattr(arguments, field)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise SmacSearchError(f"invalid SMAC argument: {field}")
    for field in ("max_wall_time_seconds", "per_trial_timeout_seconds"):
        value = getattr(arguments, field)
        if value is not None and (
            not math.isfinite(float(value)) or float(value) <= 0
        ):
            raise SmacSearchError(f"invalid SMAC argument: {field}")
    if baseline["backendRole"] != arguments.backend_role:
        raise SmacSearchError("baseline and SMAC backend roles do not match")
    if arguments.timeout_penalty < 1 or arguments.reward_clip <= 0:
        raise SmacSearchError("SMAC scoring penalties are invalid")

    scoring_policy = {
        "timeoutPenalty": arguments.timeout_penalty,
        "rewardClip": arguments.reward_clip,
        "excludeRecordedFailures": not arguments.include_recorded_failures,
    }
    host = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "node": platform.node(),
    }
    experiment = {
        "strategy": {
            "name": "smac3-algorithm-configuration-v0",
            "adapterVersion": SMAC_ADAPTER_VERSION,
            "smacVersion": SMAC_REQUIRED_VERSION,
            "seed": arguments.seed,
            "scenarioName": SMAC_SCENARIO_NAME,
            "additionalInitialConfigurations": arguments.additional_initial_configurations,
            "instanceFeaturePolicy": "one-hot-identity-v0",
            "fidelityPolicy": "disabled-until-qualified-v0",
        },
        "budget": {
            "maxTrials": arguments.max_trials,
            "maxWallTimeSeconds": arguments.max_wall_time_seconds,
            "perTrialTimeoutSeconds": arguments.per_trial_timeout_seconds,
        },
        "evaluator": {
            "version": SEARCH_EVALUATOR_VERSION,
            "tsaCliJar": str(arguments.tsa_cli_jar.resolve()),
            "tsaCliJarSha256": sha256_file(arguments.tsa_cli_jar),
            "java": arguments.java,
            "host": host,
            "backendRole": arguments.backend_role,
            "smacRepetitions": arguments.repetitions,
            "warmups": arguments.warmups,
            "progressIntervalSeconds": arguments.progress_interval_seconds,
            "parameterSpace": str(arguments.parameter_space.resolve()),
            "parameterSpaceSha256": sha256_json(parameter_space),
            "baseline": str(arguments.baseline.resolve()),
            "baselineSha256": sha256_json(baseline),
            "baselineManifest": str(arguments.baseline_manifest.resolve()),
            "baselineManifestSha256": sha256_file(arguments.baseline_manifest),
            "workload": workload,
            "scoringPolicy": scoring_policy,
        },
        "objective": {
            "name": "smac-instance-mean-normalized-cost-v0",
            "direction": "minimize",
        },
    }
    _validate_smac_experiment(experiment)
    return parameter_space, baseline, workload, experiment


def run_smac_search(arguments: argparse.Namespace) -> dict[str, Any]:
    parameter_space, baseline, workload, experiment = _prepare_experiment(arguments)
    store = SmacRunStore(arguments.run_dir)
    manifest, state, created = store.open_or_create(experiment)
    checkpoint_store = SmacNativeCheckpointStore(arguments.run_dir)
    working_directory = _working_smac_directory(arguments.run_dir, arguments.seed)
    migrated_instance_feature_pca = False
    if not created and state["smacCheckpoint"] is not None:
        checkpoint_store.restore(state["smacCheckpoint"], working_directory)
        migrated_instance_feature_pca = _migrate_smac_241_instance_feature_pca(
            working_directory
        )
        if migrated_instance_feature_pca:
            _log(
                "Migrated the restored SMAC 2.4.1 checkpoint to disable "
                "broken instance-feature PCA"
            )

    strategy = SmacAskTellStrategy.build(
        parameter_space,
        instance_ids=[entry["profileId"] for entry in workload["profiles"]],
        seed=arguments.seed,
        n_trials=arguments.max_trials,
        repetitions=arguments.repetitions,
        output_directory=arguments.run_dir.resolve() / "smac",
        scenario_name=SMAC_SCENARIO_NAME,
        crash_cost=math.exp(arguments.reward_clip) * 10.0,
        additional_initial_configurations=arguments.additional_initial_configurations,
        overwrite=False,
        logging_level=False,
    )
    strategy.restore_proposal_index(state["nextProposalIndex"])
    if state["pendingTrial"] is not None:
        strategy.attach_pending(_trial_request_from_pending(state["pendingTrial"]))
    strategy.validate_external_counts(
        completed_trials=state["completedTrialCount"],
        has_pending_trial=state["pendingTrial"] is not None,
    )
    if created:
        state["smacCheckpoint"] = checkpoint_store.capture(strategy)
        store.save_state(state)
    elif state["smacCheckpoint"] is None:
        # Only possible if the process stopped after writing the empty external
        # state but before the first native generation.
        state["smacCheckpoint"] = checkpoint_store.capture(strategy)
        store.save_state(state)
    if state["status"] == "FINISHED":
        _log(
            f"SMAC run {manifest['runId']} is already finished: {state['stopReason']}"
        )
        return state

    state["status"] = "RUNNING"
    state["stopReason"] = None
    store.save_state(state)
    store.append_event(
        "EXPERIMENT_CREATED" if created else "EXPERIMENT_RESUMED",
        compatibilityMigration=(
            "smac-2.4.1-disable-instance-feature-pca-v0"
            if migrated_instance_feature_pca
            else None
        ),
    )
    _log(
        f"{'Created' if created else 'Resumed'} {manifest['runId']}; "
        f"completed={state['completedTrialCount']}, realEvaluations={state['actualEvaluationCount']}"
    )

    cache = FileEvaluationCache(arguments.cache_dir)
    evaluator = RealCvc5ProfileTrialEvaluator(
        tsa_cli_jar=arguments.tsa_cli_jar,
        parameter_space=parameter_space,
        workload=workload,
        baseline_report=baseline,
        backend_role=arguments.backend_role,
        warmups=arguments.warmups,
        java=arguments.java,
        progress_interval_seconds=arguments.progress_interval_seconds,
        stream_child_output=not arguments.quiet_child_output,
        timeout_penalty=arguments.timeout_penalty,
        reward_clip=arguments.reward_clip,
        exclude_recorded_failures=not arguments.include_recorded_failures,
    )
    evaluator_contract = experiment["evaluator"]
    host = evaluator_contract["host"]
    scoring_policy = evaluator_contract["scoringPolicy"]
    invocation_started = time.monotonic_ns()
    accounted_invocation_nanos = 0

    def active_wall_nanos() -> int:
        return state["activeWallTimeNanos"] + (
            time.monotonic_ns() - invocation_started - accounted_invocation_nanos
        )

    def checkpoint_external() -> None:
        nonlocal accounted_invocation_nanos
        elapsed = time.monotonic_ns() - invocation_started
        delta = elapsed - accounted_invocation_nanos
        state["activeWallTimeNanos"] += max(delta, 0)
        accounted_invocation_nanos = elapsed
        store.save_state(state)

    def remaining_wall_seconds() -> float | None:
        if arguments.max_wall_time_seconds is None:
            return None
        return arguments.max_wall_time_seconds - active_wall_nanos() / 1_000_000_000

    recovered_pending = state["pendingTrial"]
    if recovered_pending is not None and recovered_pending.get("attemptState") == "RUNNING":
        last_heartbeat = recovered_pending.get(
            "attemptLastHeartbeatEpochMillis",
            recovered_pending.get("attemptStartedAtEpochMillis", int(time.time() * 1000)),
        )
        unaccounted_millis = max(int(time.time() * 1000) - last_heartbeat, 0)
        conservative_millis = min(
            unaccounted_millis,
            int(PROCESS_HEARTBEAT_INTERVAL_SECONDS * 1000),
        )
        state["activeWallTimeNanos"] += conservative_millis * 1_000_000
        recovered_pending["attemptState"] = "RECOVERED"
        checkpoint_external()
        store.append_event(
            "UNCLEAN_TRIAL_RECOVERED",
            trialId=recovered_pending["trialId"],
            requestId=recovered_pending["requestId"],
            chargedUnaccountedNanos=conservative_millis * 1_000_000,
        )

    try:
        while True:
            if state["pendingTrial"] is None and state["completedTrialCount"] >= arguments.max_trials:
                state["status"] = "FINISHED"
                state["stopReason"] = "TRIAL_BUDGET_EXHAUSTED"
                break
            remaining = remaining_wall_seconds()
            if state["pendingTrial"] is None and remaining is not None and remaining <= 0:
                state["status"] = "FINISHED"
                state["stopReason"] = "WALL_CLOCK_BUDGET_EXHAUSTED"
                break

            pending = state["pendingTrial"]
            if pending is None:
                try:
                    request = strategy.ask()
                except StopIteration:
                    state["status"] = "FINISHED"
                    state["stopReason"] = "SMAC_STOPPED"
                    break
                evaluation_identity = {
                    "evaluatorVersion": SEARCH_EVALUATOR_VERSION,
                    "searchSeed": arguments.seed,
                    "requestId": request.request_id,
                    "instanceId": request.instance_id,
                    "evaluationSeed": request.seed,
                    "repetition": request.repetition,
                    "budget": request.budget,
                    "host": host,
                    "candidate": request.candidate,
                    "parameterSpaceSha256": evaluator_contract["parameterSpaceSha256"],
                    "baselineSha256": evaluator_contract["baselineSha256"],
                    "tsaCliJarSha256": evaluator_contract["tsaCliJarSha256"],
                    "workloadSha256": workload["workloadSha256"],
                    "backendRole": arguments.backend_role,
                    "repetitions": 1,
                    "warmups": arguments.warmups,
                    "scoringPolicy": scoring_policy,
                }
                cache_key = cache.key(evaluation_identity)
                trial_id = f"trial-{request.proposal_index:06d}-{request.request_id}"
                pending = {
                    "trialId": trial_id,
                    "requestId": request.request_id,
                    "proposalIndex": request.proposal_index,
                    "candidate": request.candidate,
                    "instanceId": request.instance_id,
                    "evaluationSeed": request.seed,
                    "repetition": request.repetition,
                    "budget": request.budget,
                    "cacheKey": cache_key,
                    "evaluationIdentity": evaluation_identity,
                    "proposedAtEpochMillis": int(time.time() * 1000),
                }
                trial_dir = store.trial_directory(trial_id)
                atomic_write_json(trial_dir / "candidate.json", request.candidate)
                # Native RUNNING state is snapshotted before the external state
                # starts referring to this trial.
                descriptor = checkpoint_store.capture(strategy)
                state["smacCheckpoint"] = descriptor
                state["nextProposalIndex"] = strategy.next_proposal_index
                state["pendingTrial"] = pending
                checkpoint_external()
                store.append_event(
                    "TRIAL_PROPOSED",
                    trialId=trial_id,
                    requestId=request.request_id,
                    candidateId=request.candidate["candidateId"],
                    instanceId=request.instance_id,
                    seed=request.seed,
                    proposalIndex=request.proposal_index,
                    smacGeneration=descriptor["generation"],
                )

            trial_dir = store.trial_directory(pending["trialId"])
            request = _trial_request_from_pending(pending)
            result_path = trial_dir / "result.json"
            report_path = trial_dir / "report.json"
            score_path = trial_dir / "score.json"
            if result_path.is_file():
                result = _load_trial_result(result_path, request)
                store.append_event(
                    "TRIAL_RECOVERED",
                    trialId=pending["trialId"],
                    requestId=request.request_id,
                    source="result.json",
                )
            elif report_path.is_file() and score_path.is_file():
                report = load_json(report_path)
                score = load_json(score_path)
                result = evaluator.validate_artifacts(request, report, score).result
                atomic_write_json(result_path, asdict(result))
                store.append_event(
                    "TRIAL_RECOVERED",
                    trialId=pending["trialId"],
                    requestId=request.request_id,
                    source="report-and-score",
                )
            elif report_path.is_file():
                report = load_json(report_path)
                evaluation = evaluator.score_existing_report(request, report)
                result = evaluation.result
                atomic_write_json(score_path, evaluation.score)
                atomic_write_json(result_path, asdict(result))
                cache.put(
                    pending["cacheKey"],
                    candidate=request.candidate,
                    report=evaluation.report,
                    score=evaluation.score,
                    evaluation_identity=pending["evaluationIdentity"],
                )
                store.append_event(
                    "TRIAL_RECOVERED",
                    trialId=pending["trialId"],
                    requestId=request.request_id,
                    source="report-rescored",
                )
            else:
                cached = cache.get(pending["cacheKey"])
                if cached is not None:
                    evaluation = evaluator.validate_artifacts(
                        request, cached["report"], cached["score"]
                    )
                    result = TrialResult(**{**asdict(evaluation.result), "cache_hit": True})
                    atomic_write_json(result_path, asdict(result))
                    atomic_write_json(report_path, cached["report"])
                    atomic_write_json(score_path, cached["score"])
                    store.append_event(
                        "CACHE_HIT",
                        trialId=pending["trialId"],
                        requestId=request.request_id,
                        cacheKey=pending["cacheKey"],
                    )
                elif (
                    remaining_wall_seconds() is not None
                    and remaining_wall_seconds() <= 0
                ):
                    result = _failed_trial_result(
                        request,
                        status="INCOMPLETE_ATTEMPT",
                        duration_nanos=0,
                        failure_message="SMAC trial could not resume within the wall-clock budget",
                    )
                    atomic_write_json(result_path, asdict(result))
                else:
                    _claim_real_evaluation_number(state, pending)
                    attempt_started = int(time.time() * 1000)
                    pending["attemptState"] = "RUNNING"
                    pending["attemptStartedAtEpochMillis"] = attempt_started
                    pending["attemptLastHeartbeatEpochMillis"] = attempt_started
                    checkpoint_external()
                    store.append_event(
                        "TRIAL_STARTED",
                        trialId=pending["trialId"],
                        requestId=request.request_id,
                        realEvaluationNumber=state["actualEvaluationCount"],
                    )

                    def process_heartbeat() -> None:
                        pending["attemptLastHeartbeatEpochMillis"] = int(time.time() * 1000)
                        checkpoint_external()

                    remaining = remaining_wall_seconds()
                    timeouts = [
                        value
                        for value in (arguments.per_trial_timeout_seconds, remaining)
                        if value is not None
                    ]
                    process_timeout = max(min(timeouts), 0.001) if timeouts else None
                    _log(
                        f"SMAC trial {state['completedTrialCount'] + 1}/{arguments.max_trials}: "
                        f"{request.candidate['candidateId']} on {request.instance_id}, seed={request.seed}"
                    )
                    started_nanos = time.monotonic_ns()
                    try:
                        evaluation = evaluator.evaluate(
                            request,
                            report_path,
                            process_timeout,
                            process_heartbeat,
                        )
                        result = evaluation.result
                        atomic_write_json(score_path, evaluation.score)
                        cache.put(
                            pending["cacheKey"],
                            candidate=request.candidate,
                            report=evaluation.report,
                            score=evaluation.score,
                            evaluation_identity=pending["evaluationIdentity"],
                        )
                    except CandidateDisqualifiedError as failure:
                        result = failure.result
                        _write_trial_failure(
                            trial_dir,
                            candidate_id=result.candidate_id,
                            status=result.status,
                            failure=failure,
                            process_timeout_seconds=process_timeout,
                        )
                    except ReplayTimeoutError as failure:
                        result = _failed_trial_result(
                            request,
                            status="CANDIDATE_TIMEOUT",
                            duration_nanos=time.monotonic_ns() - started_nanos,
                            censored=True,
                            failure_message=str(failure),
                        )
                        _write_trial_failure(
                            trial_dir,
                            candidate_id=result.candidate_id,
                            status=result.status,
                            failure=failure,
                            process_timeout_seconds=process_timeout,
                        )
                    except ReplayProcessError as failure:
                        status = "NATIVE_CRASH" if failure.signal_number is not None else "JVM_FAILURE"
                        result = _failed_trial_result(
                            request,
                            status=status,
                            duration_nanos=time.monotonic_ns() - started_nanos,
                            failure_message=str(failure),
                        )
                        _write_trial_failure(
                            trial_dir,
                            candidate_id=result.candidate_id,
                            status=status,
                            failure=failure,
                            process_timeout_seconds=process_timeout,
                            return_code=failure.return_code,
                            signal_number=failure.signal_number,
                        )
                    except InvalidReplayReportError as failure:
                        result = _failed_trial_result(
                            request,
                            status="INVALID_REPORT",
                            duration_nanos=time.monotonic_ns() - started_nanos,
                            failure_message=str(failure),
                        )
                        _write_trial_failure(
                            trial_dir,
                            candidate_id=result.candidate_id,
                            status=result.status,
                            failure=failure,
                            process_timeout_seconds=process_timeout,
                        )
                    except OSError as failure:
                        result = _failed_trial_result(
                            request,
                            status="INFRASTRUCTURE_FAILURE",
                            duration_nanos=time.monotonic_ns() - started_nanos,
                            failure_message=str(failure),
                        )
                        _write_trial_failure(
                            trial_dir,
                            candidate_id=result.candidate_id,
                            status=result.status,
                            failure=failure,
                            process_timeout_seconds=process_timeout,
                        )
                    except EvaluatorError as failure:
                        result = _failed_trial_result(
                            request,
                            status="EVALUATOR_FAILURE",
                            duration_nanos=time.monotonic_ns() - started_nanos,
                            failure_message=str(failure),
                        )
                        _write_trial_failure(
                            trial_dir,
                            candidate_id=result.candidate_id,
                            status=result.status,
                            failure=failure,
                            process_timeout_seconds=process_timeout,
                        )
                    atomic_write_json(result_path, asdict(result))

            previous_incumbent = state["incumbent"]
            strategy.tell(result)
            descriptor = checkpoint_store.capture(strategy)
            state["smacCheckpoint"] = descriptor
            state["completedRequestIds"].append(request.request_id)
            state["completedTrialCount"] += 1
            if result.cache_hit:
                state["cacheHitCount"] += 1
            state["pendingTrial"] = None
            state["incumbent"] = strategy.incumbent_snapshot()
            strategy.validate_external_counts(
                completed_trials=state["completedTrialCount"],
                has_pending_trial=False,
            )
            checkpoint_external()
            store.append_event(
                "TRIAL_FINISHED",
                trialId=pending["trialId"],
                requestId=request.request_id,
                result=asdict(result),
                smacGeneration=descriptor["generation"],
            )
            if state["incumbent"] != previous_incumbent:
                store.append_event("INCUMBENT_CHANGED", incumbent=state["incumbent"])
            cost_text = "n/a" if result.cost is None else f"{result.cost:.6f}"
            _log(
                f"Finished {request.request_id}: status={result.status}, "
                f"cost={cost_text}, cacheHit={result.cache_hit}"
            )
    except (KeyboardInterrupt, ExperimentInterrupted) as interruption:
        state["status"] = "INTERRUPTED"
        reason = (
            interruption.signal_name
            if isinstance(interruption, ExperimentInterrupted)
            else "SIGINT"
        )
        state["stopReason"] = reason
        if state["pendingTrial"] is not None:
            state["pendingTrial"]["attemptState"] = "INTERRUPTED"
            state["pendingTrial"]["attemptLastHeartbeatEpochMillis"] = int(
                time.time() * 1000
            )
        checkpoint_external()
        store.append_event(
            "EXPERIMENT_INTERRUPTED",
            signal=reason,
            pendingTrial=state["pendingTrial"],
        )
        _log("SMAC interrupted; durable external and native checkpoints retained")
        raise

    checkpoint_external()
    store.append_event(
        "EXPERIMENT_FINISHED",
        stopReason=state["stopReason"],
        completedTrialCount=state["completedTrialCount"],
        actualEvaluationCount=state["actualEvaluationCount"],
        incumbent=state["incumbent"],
    )
    _log(
        f"SMAC finished: reason={state['stopReason']}, "
        f"trials={state['completedTrialCount']}, "
        f"realEvaluations={state['actualEvaluationCount']}, "
        f"cacheHits={state['cacheHitCount']}"
    )
    return state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run or resume durable SMAC3 with real cvc5")
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--cache-dir", type=Path, default=Path("experiments/cache-v0"))
    run.add_argument("--parameter-space", type=Path, required=True)
    run.add_argument("--baseline", type=Path, required=True)
    run.add_argument("--baseline-manifest", type=Path, required=True)
    run.add_argument("--tsa-cli-jar", type=Path, required=True)
    run.add_argument("--profile-dir", type=Path)
    run.add_argument("--profile", type=Path, action="append", default=[])
    run.add_argument("--backend-role", default="regular-bv")
    run.add_argument("--repetitions", type=int, default=1)
    run.add_argument("--warmups", type=int, default=0)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--additional-initial-configurations", type=int, default=8)
    run.add_argument("--max-trials", type=int, required=True)
    run.add_argument("--max-wall-time-seconds", type=float)
    run.add_argument("--per-trial-timeout-seconds", type=float)
    run.add_argument("--timeout-penalty", type=float, default=10.0)
    run.add_argument("--reward-clip", type=float, default=5.0)
    run.add_argument("--include-recorded-failures", action="store_true")
    run.add_argument("--progress-interval-seconds", type=int, default=15)
    run.add_argument("--quiet-child-output", action="store_true")
    run.add_argument("--java", default="java")

    status = commands.add_parser("status", help="print the external SMAC checkpoint")
    status.add_argument("--run-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    previous_handlers: dict[int, Any] = {}

    def interrupt(signum: int, _frame: Any) -> None:
        raise ExperimentInterrupted(signal.Signals(signum).name)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    try:
        if arguments.command == "status":
            manifest = load_json(arguments.run_dir / "manifest.json")
            SmacRunStore._validate_manifest(manifest)
            state = load_json(arguments.run_dir / "state.json")
            SmacRunStore._validate_state(state, manifest["runId"])
            if state["smacCheckpoint"] is not None:
                SmacNativeCheckpointStore(arguments.run_dir).validate(
                    state["smacCheckpoint"]
                )
            print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        run_smac_search(arguments)
        return 0
    except (KeyboardInterrupt, ExperimentInterrupted):
        return 130
    except (EvaluatorError, SearchError, SmacAdapterError, OSError) as failure:
        print(f"SMAC search failed: {failure}", file=sys.stderr)
        return 2
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
