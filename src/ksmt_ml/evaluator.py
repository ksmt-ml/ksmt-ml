"""Offline cvc5 profile replay and reward calculation.

The JVM runner owns exact KSMT AST decoding and cvc5 execution.  This module
owns experiment-facing candidate validation, process isolation, and scoring.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


CANDIDATE_SCHEMA_VERSION = "cvc5-replay-candidate-v0"
REPORT_SCHEMA_VERSION = "tsa-cvc5-profile-replay-report-v0"
RESULT_SCHEMA_VERSION = "tsa-cvc5-profile-replay-result-v0"
SCORE_SCHEMA_VERSION = "cvc5-evaluator-score-v0"
BASELINE_MANIFEST_SCHEMA_VERSION = "cvc5-baseline-manifest-v0"
WORKLOAD_MANIFEST_SCHEMA_VERSION = "cvc5-workload-manifest-v0"
REPLAY_EXECUTION_MODE = "per-profile-process-v0"

PROTECTED_CVC5_OPTIONS = frozenset(
    {
        "logic",
        "tlimit-per",
        "produce-models",
        "produce-unsat-cores",
        "incremental",
        "arrays-exp",
        "fp-exp",
    }
)


class EvaluatorError(ValueError):
    """The candidate, report, or requested evaluation is invalid."""


class ReplayTimeoutError(EvaluatorError):
    """A candidate exhausted its whole-process evaluation budget."""

    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"TSA replay exceeded the {timeout_seconds}-second process budget"
        )


class ReplayProcessError(EvaluatorError):
    """The isolated JVM exited before it produced a valid report."""

    def __init__(self, return_code: int, detail: str):
        self.return_code = return_code
        self.signal_number = -return_code if return_code < 0 else None
        suffix = f": {detail}" if detail else ""
        super().__init__(f"TSA replay exited with code {return_code}{suffix}")


class InvalidReplayReportError(EvaluatorError):
    """The isolated evaluator emitted a malformed or inconsistent report."""


class ExperimentInterrupted(Exception):
    """The experiment received a user or service termination request."""

    def __init__(self, signal_name: str):
        self.signal_name = signal_name
        super().__init__(f"experiment interrupted by {signal_name}")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as source:
            value = json.load(source)
    except json.JSONDecodeError as failure:
        raise EvaluatorError(f"invalid JSON in {path}: {failure}") from failure
    if not isinstance(value, dict):
        raise EvaluatorError(f"expected a JSON object in {path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as destination:
            json.dump(value, destination, indent=2, sort_keys=True)
            destination.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _profile_header(path: Path) -> dict[str, Any]:
    """Read profile provenance without materializing its potentially huge AST stream."""

    prefix: list[str] = []
    found_events = False
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.lstrip().startswith('"events"'):
                indentation = line[: len(line) - len(line.lstrip())]
                prefix.append(f'{indentation}"events": []\n')
                found_events = True
                break
            prefix.append(line)
    if not found_events:
        raise EvaluatorError(f"profile has no events field: {path}")
    prefix.append("}\n")
    try:
        header = json.loads("".join(prefix))
    except json.JSONDecodeError as failure:
        raise EvaluatorError(f"invalid profile header in {path}: {failure}") from failure
    if not isinstance(header, dict):
        raise EvaluatorError(f"profile root must be an object: {path}")
    return header


def describe_workload(
    *,
    profile_directory: Path | None = None,
    profiles: Sequence[Path] = (),
    backend_role: str,
) -> dict[str, Any]:
    """Create a stable content-addressed description of the profiles TSA will replay."""

    if backend_role not in {"regular-bv", "integer-blasted"}:
        raise EvaluatorError(f"unsupported backend role: {backend_role}")
    selected = [path.resolve() for path in profiles]
    if profile_directory is not None:
        selected.extend(
            path.resolve()
            for path in sorted(profile_directory.resolve().glob("*.json"))
            if path.is_file()
        )
    unique = {str(path): path for path in selected}
    if not unique:
        raise EvaluatorError("no replay profiles were selected")

    entries: list[dict[str, Any]] = []
    file_names: set[str] = set()
    profile_ids: set[str] = set()
    for path in (unique[key] for key in sorted(unique)):
        if not path.is_file():
            raise EvaluatorError(f"profile does not exist: {path}")
        header = _profile_header(path)
        solver = header.get("solver")
        attributes = header.get("attributes")
        producer = header.get("producer")
        if not isinstance(solver, dict) or not isinstance(attributes, dict):
            raise EvaluatorError(f"profile has incomplete solver provenance: {path}")
        actual_role = solver.get("attributes", {}).get("backend-role")
        if actual_role != backend_role:
            continue
        profile_id = _require_non_empty_string(header, "profileId")
        run_id = _require_non_empty_string(header, "runId")
        workload_id = attributes.get("workload-id")
        if not isinstance(workload_id, str) or not workload_id:
            raise EvaluatorError(f"profile has no workload-id: {path}")
        if path.name in file_names:
            raise EvaluatorError(f"duplicate profile file name: {path.name}")
        if profile_id in profile_ids:
            raise EvaluatorError(f"duplicate profileId: {profile_id}")
        file_names.add(path.name)
        profile_ids.add(profile_id)
        entries.append(
            {
                "fileName": path.name,
                "path": str(path),
                "sha256": sha256_file(path),
                "sizeBytes": path.stat().st_size,
                "profileId": profile_id,
                "runId": run_id,
                "workloadId": workload_id,
                "solverName": solver.get("name"),
                "solverVersion": solver.get("version"),
                "backendRole": actual_role,
                "profileSchemaVersion": header.get("schemaVersion"),
                "producerFormat": producer.get("format") if isinstance(producer, dict) else None,
                "captureComplete": (
                    producer.get("captureComplete") if isinstance(producer, dict) else None
                ),
                "replayable": producer.get("replayable") if isinstance(producer, dict) else None,
            }
        )
    if not entries:
        raise EvaluatorError(f"no {backend_role} profiles were found")
    stable_entries = [
        {key: value for key, value in entry.items() if key != "path"}
        for entry in entries
    ]
    workload_sha256 = sha256_json(
        {"backendRole": backend_role, "profiles": stable_entries}
    )
    return {
        "schemaVersion": WORKLOAD_MANIFEST_SCHEMA_VERSION,
        "backendRole": backend_role,
        "workloadSha256": workload_sha256,
        "profiles": entries,
    }


def _stable_workload(workload: Mapping[str, Any]) -> dict[str, Any]:
    profiles = workload.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise EvaluatorError("workload manifest must contain profiles")
    return {
        "backendRole": workload.get("backendRole"),
        "profiles": [
            {key: value for key, value in entry.items() if key != "path"}
            for entry in profiles
        ],
    }


def validate_workload_manifest(workload: Mapping[str, Any]) -> None:
    if set(workload) != {
        "schemaVersion",
        "backendRole",
        "workloadSha256",
        "profiles",
    }:
        raise EvaluatorError("workload manifest fields do not match the v0 contract")
    _require_equal(workload, "schemaVersion", WORKLOAD_MANIFEST_SCHEMA_VERSION)
    if workload.get("backendRole") not in {"regular-bv", "integer-blasted"}:
        raise EvaluatorError("workload manifest has invalid backendRole")
    profiles = workload.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise EvaluatorError("workload manifest must contain profiles")
    names: set[str] = set()
    ids: set[str] = set()
    expected_entry_fields = {
        "fileName",
        "path",
        "sha256",
        "sizeBytes",
        "profileId",
        "runId",
        "workloadId",
        "solverName",
        "solverVersion",
        "backendRole",
        "profileSchemaVersion",
        "producerFormat",
        "captureComplete",
        "replayable",
    }
    for entry in profiles:
        if not isinstance(entry, dict):
            raise EvaluatorError("workload profile entry must be an object")
        if set(entry) != expected_entry_fields:
            raise EvaluatorError("workload profile fields do not match the v0 contract")
        for key in (
            "fileName",
            "path",
            "sha256",
            "profileId",
            "runId",
            "workloadId",
            "solverName",
            "solverVersion",
            "backendRole",
            "profileSchemaVersion",
            "producerFormat",
        ):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise EvaluatorError(f"workload profile has invalid {key}")
        if entry["backendRole"] != workload["backendRole"]:
            raise EvaluatorError("workload profile backendRole mismatch")
        if entry["solverName"] != "cvc5":
            raise EvaluatorError("workload contains a profile from a non-cvc5 solver")
        if entry["profileSchemaVersion"] != "ksmt-solver-profile-v0":
            raise EvaluatorError("workload contains an unsupported profile schema")
        if entry["producerFormat"] != "tsa-solver-profile-json-v0":
            raise EvaluatorError("workload contains an unsupported producer format")
        if not Path(entry["path"]).is_absolute():
            raise EvaluatorError("workload profile path must be absolute")
        if not isinstance(entry.get("sizeBytes"), int) or entry["sizeBytes"] < 0:
            raise EvaluatorError("workload profile has invalid sizeBytes")
        if not isinstance(entry.get("captureComplete"), bool) or not entry["captureComplete"]:
            raise EvaluatorError("workload contains an incomplete profile")
        if not isinstance(entry.get("replayable"), bool) or not entry["replayable"]:
            raise EvaluatorError("workload contains a non-replayable profile")
        if not _is_sha256(entry.get("sha256")):
            raise EvaluatorError("workload profile has invalid sha256")
        if entry["fileName"] in names or entry["profileId"] in ids:
            raise EvaluatorError("workload manifest contains duplicate profiles")
        names.add(entry["fileName"])
        ids.add(entry["profileId"])
    expected = sha256_json(_stable_workload(workload))
    if workload.get("workloadSha256") != expected:
        raise EvaluatorError("workload manifest digest mismatch")


def single_profile_workload(
    workload: Mapping[str, Any], profile_id: str
) -> dict[str, Any]:
    """Project a validated workload manifest onto one profile instance."""

    validate_workload_manifest(workload)
    matches = [entry for entry in workload["profiles"] if entry["profileId"] == profile_id]
    if len(matches) != 1:
        raise EvaluatorError(f"workload has no unique profileId: {profile_id}")
    entry = dict(matches[0])
    stable_entry = {key: value for key, value in entry.items() if key != "path"}
    return {
        "schemaVersion": workload["schemaVersion"],
        "backendRole": workload["backendRole"],
        "workloadSha256": sha256_json(
            {"backendRole": workload["backendRole"], "profiles": [stable_entry]}
        ),
        "profiles": [entry],
    }


def build_baseline_manifest(
    *,
    report_path: Path,
    report: Mapping[str, Any],
    parameter_space_path: Path,
    parameter_space: Mapping[str, Any],
    tsa_cli_jar: Path,
    workload: Mapping[str, Any],
) -> dict[str, Any]:
    validate_baseline_preflight(report, parameter_space, workload)
    return {
        "schemaVersion": BASELINE_MANIFEST_SCHEMA_VERSION,
        "executionMode": REPLAY_EXECUTION_MODE,
        "createdAtEpochMillis": int(time.time() * 1000),
        "baselineReport": {
            "fileName": report_path.resolve().name,
            "sha256": sha256_file(report_path.resolve()),
        },
        "parameterSpace": {
            "parameterSpaceId": parameter_space["parameterSpaceId"],
            "fileName": parameter_space_path.resolve().name,
            "sha256": sha256_file(parameter_space_path.resolve()),
        },
        "tsaCliJar": {
            "fileName": tsa_cli_jar.resolve().name,
            "sha256": sha256_file(tsa_cli_jar.resolve()),
        },
        "defaultCandidate": report["candidate"],
        "workload": dict(workload),
    }


def validate_baseline_manifest(
    manifest: Mapping[str, Any],
    *,
    report_path: Path,
    report: Mapping[str, Any],
    parameter_space_path: Path,
    parameter_space: Mapping[str, Any],
    tsa_cli_jar: Path,
    current_workload: Mapping[str, Any],
) -> None:
    required = {
        "schemaVersion",
        "executionMode",
        "createdAtEpochMillis",
        "baselineReport",
        "parameterSpace",
        "tsaCliJar",
        "defaultCandidate",
        "workload",
    }
    if set(manifest) != required:
        raise EvaluatorError("baseline manifest fields do not match the v0 contract")
    _require_equal(manifest, "schemaVersion", BASELINE_MANIFEST_SCHEMA_VERSION)
    _require_equal(manifest, "executionMode", REPLAY_EXECUTION_MODE)
    _non_negative_int(manifest, "createdAtEpochMillis")
    validate_workload_manifest(current_workload)
    stored_workload = manifest.get("workload")
    if not isinstance(stored_workload, dict):
        raise EvaluatorError("baseline manifest has no workload")
    validate_workload_manifest(stored_workload)
    if _stable_workload(stored_workload) != _stable_workload(current_workload):
        raise EvaluatorError("baseline and current workload profiles differ")
    if stored_workload.get("workloadSha256") != current_workload.get("workloadSha256"):
        raise EvaluatorError("baseline and current workload hashes differ")
    _validate_file_binding(manifest.get("baselineReport"), report_path, "baseline report")
    _validate_file_binding(manifest.get("parameterSpace"), parameter_space_path, "parameter space")
    if manifest["parameterSpace"].get("parameterSpaceId") != parameter_space.get(
        "parameterSpaceId"
    ):
        raise EvaluatorError("baseline manifest parameterSpaceId mismatch")
    _validate_file_binding(manifest.get("tsaCliJar"), tsa_cli_jar, "TSA CLI JAR")
    if manifest.get("defaultCandidate") != report.get("candidate"):
        raise EvaluatorError("baseline manifest candidate mismatch")
    normalize_candidate(manifest["defaultCandidate"], parameter_space)
    validate_baseline_preflight(report, parameter_space, current_workload)


def _validate_file_binding(binding: Any, path: Path, label: str) -> None:
    if not isinstance(binding, dict):
        raise EvaluatorError(f"baseline manifest has no {label} binding")
    expected_fields = {"fileName", "sha256"}
    if label == "parameter space":
        expected_fields.add("parameterSpaceId")
    if set(binding) != expected_fields:
        raise EvaluatorError(f"baseline manifest {label} binding is malformed")
    if binding.get("fileName") != path.resolve().name:
        raise EvaluatorError(f"baseline manifest {label} file name mismatch")
    if not _is_sha256(binding.get("sha256")):
        raise EvaluatorError(f"baseline manifest {label} has invalid sha256")
    if sha256_file(path.resolve()) != binding["sha256"]:
        raise EvaluatorError(f"baseline manifest {label} content mismatch")


def normalize_candidate(
    candidate: Mapping[str, Any],
    parameter_space: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _require_equal(candidate, "schemaVersion", CANDIDATE_SCHEMA_VERSION)
    _require_equal(candidate, "targetSolver", "cvc5")
    candidate_id = _require_non_empty_string(candidate, "candidateId")
    parameter_space_id = _require_non_empty_string(candidate, "parameterSpaceId")
    options = candidate.get("options")
    if not isinstance(options, dict):
        raise EvaluatorError("candidate options must be an object")

    unknown_protected = sorted(PROTECTED_CVC5_OPTIONS.intersection(options))
    if unknown_protected:
        raise EvaluatorError(
            "candidate changes evaluator-controlled options: "
            + ", ".join(unknown_protected)
        )

    if parameter_space is not None:
        validate_parameter_space(parameter_space, parameter_space_id)
        domains = parameter_space["options"]
        unknown = sorted(set(options).difference(domains))
        if unknown:
            raise EvaluatorError(
                "candidate contains options outside the parameter space: "
                + ", ".join(unknown)
            )
        for name, value in options.items():
            _validate_domain_value(name, value, domains[name])
        effective_values = _effective_option_values(domains, options)
        active = active_option_names(parameter_space, options)
        inactive = sorted(set(options).difference(active))
        if inactive:
            raise EvaluatorError(
                "candidate sets conditionally inactive options: " + ", ".join(inactive)
            )
        for constraint in parameter_space.get("constraints", []):
            if _evaluate_predicate(constraint["when"], effective_values):
                raise EvaluatorError(
                    f"candidate violates parameter-space constraint: {constraint['reason']}"
                )

    normalized_options = {
        name: _cvc5_option_string(name, value)
        for name, value in sorted(options.items())
    }
    return {
        "schemaVersion": CANDIDATE_SCHEMA_VERSION,
        "candidateId": candidate_id,
        "parameterSpaceId": parameter_space_id,
        "targetSolver": "cvc5",
        "options": normalized_options,
    }


def run_replay(
    *,
    tsa_cli_jar: Path,
    candidate: Mapping[str, Any],
    output_path: Path,
    profile_directory: Path | None = None,
    profiles: Sequence[Path] = (),
    backend_role: str = "regular-bv",
    repetitions: int = 1,
    warmups: int = 0,
    java: str = "java",
    process_timeout_seconds: float | None = None,
    parameter_space: Mapping[str, Any] | None = None,
    stream_child_output: bool = True,
    progress_interval_seconds: int = 15,
    process_heartbeat: Callable[[], None] | None = None,
    process_heartbeat_interval_seconds: float = 5.0,
) -> dict[str, Any]:
    if profile_directory is None and not profiles:
        raise EvaluatorError("a profile directory or at least one profile is required")
    if repetitions <= 0:
        raise EvaluatorError("repetitions must be positive")
    if warmups < 0:
        raise EvaluatorError("warmups must not be negative")
    if progress_interval_seconds < 0:
        raise EvaluatorError("progress interval must not be negative")
    if process_heartbeat_interval_seconds <= 0:
        raise EvaluatorError("process heartbeat interval must be positive")
    if backend_role not in {"regular-bv", "integer-blasted"}:
        raise EvaluatorError(f"unsupported backend role: {backend_role}")

    normalized = normalize_candidate(candidate, parameter_space)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="ksmt-ml-evaluator-") as temporary_dir:
        candidate_path = Path(temporary_dir) / "candidate.json"
        write_json(candidate_path, normalized)

        command = [
            java,
            "-jar",
            str(tsa_cli_jar.resolve()),
            "profile-replay",
            "--candidate",
            str(candidate_path),
            "--output",
            str(output_path),
            "--backend-role",
            backend_role,
            "--repetitions",
            str(repetitions),
            "--warmups",
            str(warmups),
            "--progress-interval-seconds",
            str(progress_interval_seconds),
        ]
        if profile_directory is not None:
            command.extend(("--profile-dir", str(profile_directory.resolve())))
        for profile in profiles:
            command.extend(("--profile", str(profile.resolve())))

        stdout_path = Path(temporary_dir) / "stdout.log"
        stderr_path = Path(temporary_dir) / "stderr.log"
        stdout_file = None
        stderr_file = None
        try:
            if not stream_child_output:
                stdout_file = stdout_path.open("w", encoding="utf-8")
                stderr_file = stderr_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                start_new_session=True,
                preexec_fn=_set_linux_parent_death_signal if sys.platform.startswith("linux") else None,
            )
            started = time.monotonic()
            next_heartbeat = started + process_heartbeat_interval_seconds
            try:
                while True:
                    elapsed = time.monotonic() - started
                    remaining = (
                        None
                        if process_timeout_seconds is None
                        else process_timeout_seconds - elapsed
                    )
                    if remaining is not None and remaining <= 0:
                        raise ReplayTimeoutError(process_timeout_seconds)
                    wait_seconds = min(
                        process_heartbeat_interval_seconds,
                        remaining if remaining is not None else process_heartbeat_interval_seconds,
                    )
                    try:
                        return_code = process.wait(timeout=max(wait_seconds, 0.001))
                        break
                    except subprocess.TimeoutExpired:
                        if process_heartbeat is not None and time.monotonic() >= next_heartbeat:
                            process_heartbeat()
                            next_heartbeat = time.monotonic() + process_heartbeat_interval_seconds
            except BaseException:
                _terminate_process_group(process)
                raise
        finally:
            if stdout_file is not None:
                stdout_file.close()
            if stderr_file is not None:
                stderr_file.close()

        if return_code != 0:
            detail = ""
            if not stream_child_output:
                stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
                stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
                detail = (stderr or stdout).strip()
            if len(detail) > 4_000:
                detail = detail[-4_000:]
            raise ReplayProcessError(return_code, detail)

    if not output_path.is_file():
        raise EvaluatorError("TSA replay did not produce its report")
    try:
        report = load_json(output_path)
        validate_report(report, expected_candidate_id=normalized["candidateId"])
    except EvaluatorError as failure:
        raise InvalidReplayReportError(str(failure)) from failure
    if parameter_space is not None:
        compatible_versions = set(parameter_space["compatibleSolverVersions"])
        incompatible = sorted(
            {
                result.get("solverVersion")
                for result in report["results"]
                if result.get("solverVersion") not in compatible_versions
            },
            key=str,
        )
        if incompatible:
            raise EvaluatorError(
                "profile solver versions are outside the parameter space: "
                + ", ".join(map(str, incompatible))
            )
    return report


def run_profile_replay_matrix(
    *,
    tsa_cli_jar: Path,
    candidate: Mapping[str, Any],
    parameter_space: Mapping[str, Any],
    output_path: Path,
    profile_directory: Path | None = None,
    profiles: Sequence[Path] = (),
    backend_role: str = "regular-bv",
    repetitions: int = 1,
    warmups: int = 0,
    java: str = "java",
    process_timeout_seconds: float | None = None,
    stream_child_output: bool = True,
    progress_interval_seconds: int = 15,
) -> dict[str, Any]:
    """Run a complete workload as isolated one-profile/one-repetition trials."""

    workload = describe_workload(
        profile_directory=profile_directory,
        profiles=profiles,
        backend_role=backend_role,
    )
    normalized = normalize_candidate(candidate, parameter_space)
    started = time.monotonic()
    trial_root = output_path.resolve().parent / f"{output_path.stem}.profile-trials"
    trial_reports: list[tuple[str, int, Mapping[str, Any]]] = []
    profile_file_names: dict[str, str] = {}
    for profile_index, entry in enumerate(workload["profiles"]):
        profile_id = entry["profileId"]
        profile_file_names[profile_id] = entry["fileName"]
        for repetition in range(repetitions):
            trial_path = trial_root / (
                f"{profile_index:04d}-{sha256_json(profile_id)[:12]}-r{repetition:04d}.json"
            )
            if trial_path.is_file():
                trial_report = load_json(trial_path)
                validate_report_against_workload(
                    trial_report,
                    parameter_space,
                    single_profile_workload(workload, profile_id),
                    expected_repetitions=1,
                    expected_warmups=warmups,
                )
                if trial_report["candidate"] != normalized:
                    raise EvaluatorError("persisted profile trial belongs to another candidate")
            else:
                remaining = None
                if process_timeout_seconds is not None:
                    remaining = process_timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise ReplayTimeoutError(process_timeout_seconds)
                trial_report = run_replay(
                    tsa_cli_jar=tsa_cli_jar,
                    candidate=normalized,
                    parameter_space=parameter_space,
                    output_path=trial_path,
                    profiles=[Path(entry["path"])],
                    backend_role=backend_role,
                    repetitions=1,
                    warmups=warmups,
                    java=java,
                    process_timeout_seconds=remaining,
                    stream_child_output=stream_child_output,
                    progress_interval_seconds=progress_interval_seconds,
                )
            trial_reports.append((profile_id, repetition, trial_report))

    report = aggregate_profile_trial_reports(
        candidate=normalized,
        backend_role=backend_role,
        repetitions=repetitions,
        warmups=warmups,
        profile_file_names=profile_file_names,
        trials=trial_reports,
    )
    write_json(output_path.resolve(), report)
    return report


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def _set_linux_parent_death_signal() -> None:
    """Ensure a SIGKILLed Python evaluator cannot orphan the TSA JVM on Linux."""

    parent_pid = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    pr_set_pdeathsig = 1
    if libc.prctl(pr_set_pdeathsig, signal.SIGKILL, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if os.getppid() != parent_pid:
        os.kill(os.getpid(), signal.SIGKILL)


def validate_report(
    report: Mapping[str, Any],
    *,
    expected_candidate_id: str | None = None,
) -> None:
    required_report_fields = {
        "schemaVersion",
        "createdAtEpochMillis",
        "candidate",
        "backendRole",
        "repetitions",
        "warmupRepetitions",
        "sourceProfiles",
        "results",
    }
    if set(report) != required_report_fields:
        raise EvaluatorError("replay report fields do not match the v0 contract")
    _require_equal(report, "schemaVersion", REPORT_SCHEMA_VERSION)
    _non_negative_int(report, "createdAtEpochMillis")
    candidate = report.get("candidate")
    if not isinstance(candidate, dict):
        raise EvaluatorError("replay report candidate must be an object")
    normalized = normalize_candidate(candidate)
    if candidate != normalized:
        raise EvaluatorError("replay report candidate is not canonically encoded")
    if expected_candidate_id is not None and normalized["candidateId"] != expected_candidate_id:
        raise EvaluatorError("replay report belongs to another candidate")
    backend_role = report.get("backendRole")
    if backend_role not in {"regular-bv", "integer-blasted"}:
        raise EvaluatorError("replay report has invalid backendRole")
    repetitions = report.get("repetitions")
    warmups = report.get("warmupRepetitions")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions <= 0:
        raise EvaluatorError("replay report repetitions must be positive")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise EvaluatorError("replay report warmupRepetitions must be non-negative")
    source_profiles = report.get("sourceProfiles")
    if (
        not isinstance(source_profiles, list)
        or not source_profiles
        or any(not isinstance(name, str) or not name for name in source_profiles)
        or len(source_profiles) != len(set(source_profiles))
    ):
        raise EvaluatorError("replay report sourceProfiles are invalid")
    results = report.get("results")
    if not isinstance(results, list) or not results:
        raise EvaluatorError("replay report must contain at least one result")
    if len(results) != len(source_profiles) * repetitions:
        raise EvaluatorError("replay report result count does not match profiles x repetitions")
    required_result_fields = {
        "schemaVersion",
        "candidateId",
        "parameterSpaceId",
        "profileId",
        "runId",
        "solverVersion",
        "backendRole",
        "repetition",
        "outcome",
        "wallDurationNanos",
        "decodeDurationNanos",
        "solverOperationDurationNanos",
        "nonCheckDurationNanos",
        "knownCheckDurationNanos",
        "unknownCheckDurationNanos",
        "knownStatusTimeoutDurationNanos",
        "knownStatusTimeoutBudgetNanos",
        "eventCount",
        "executedEventCount",
        "recordedFailureEventCount",
        "skippedResultAccessCount",
        "checkCount",
        "knownStatusMatchCount",
        "knownStatusTimeoutCount",
        "wrongAnswerCount",
        "expectedUnknownCount",
        "resolvedUnknownCount",
        "actualStatusCounts",
        "operationDurationNanos",
        "mismatches",
    }
    optional_result_fields = {"failureType", "failureMessage"}
    seen_repetitions: set[tuple[str, int]] = set()
    repetitions_by_profile: dict[str, set[int]] = defaultdict(set)
    for result in results:
        if not isinstance(result, dict):
            raise EvaluatorError("replay result must be an object")
        if not required_result_fields.issubset(result) or set(result).difference(
            required_result_fields | optional_result_fields
        ):
            raise EvaluatorError("replay result fields do not match the v0 contract")
        _require_equal(result, "schemaVersion", RESULT_SCHEMA_VERSION)
        if result.get("candidateId") != normalized["candidateId"]:
            raise EvaluatorError("replay result candidate id does not match report")
        if result.get("parameterSpaceId") != normalized["parameterSpaceId"]:
            raise EvaluatorError("replay result parameterSpaceId does not match report")
        profile_id = _require_non_empty_string(result, "profileId")
        _require_non_empty_string(result, "runId")
        solver_version = result.get("solverVersion")
        if solver_version is not None and (
            not isinstance(solver_version, str) or not solver_version
        ):
            raise EvaluatorError("replay result has invalid solverVersion")
        if result.get("backendRole") != backend_role:
            raise EvaluatorError("replay result backendRole does not match report")
        repetition = _non_negative_int(result, "repetition")
        if repetition >= repetitions:
            raise EvaluatorError("replay result repetition is outside report range")
        identity = (profile_id, repetition)
        if identity in seen_repetitions:
            raise EvaluatorError("duplicate profile repetition in replay report")
        seen_repetitions.add(identity)
        repetitions_by_profile[profile_id].add(repetition)
        if result.get("outcome") not in {"SUCCESS", "FAILURE"}:
            raise EvaluatorError("replay result has invalid outcome")
        for field in (
            "wallDurationNanos",
            "decodeDurationNanos",
            "solverOperationDurationNanos",
            "nonCheckDurationNanos",
            "knownCheckDurationNanos",
            "unknownCheckDurationNanos",
            "knownStatusTimeoutDurationNanos",
            "knownStatusTimeoutBudgetNanos",
            "eventCount",
            "executedEventCount",
            "recordedFailureEventCount",
            "skippedResultAccessCount",
            "checkCount",
            "knownStatusMatchCount",
            "knownStatusTimeoutCount",
            "wrongAnswerCount",
            "expectedUnknownCount",
            "resolvedUnknownCount",
        ):
            _non_negative_int(result, field)
        if result["executedEventCount"] > result["eventCount"]:
            raise EvaluatorError("executedEventCount exceeds eventCount")
        if result["knownStatusTimeoutDurationNanos"] > result["knownCheckDurationNanos"]:
            raise EvaluatorError("known timeout duration exceeds known check duration")
        for field in ("actualStatusCounts", "operationDurationNanos"):
            _validate_non_negative_integer_map(result.get(field), field)
        mismatches = result.get("mismatches")
        if not isinstance(mismatches, list) or len(mismatches) > 20:
            raise EvaluatorError("replay result mismatches are invalid")
        for mismatch in mismatches:
            _validate_mismatch(mismatch)
        for field in optional_result_fields:
            value = result.get(field)
            if value is not None and not isinstance(value, str):
                raise EvaluatorError(f"replay result has invalid {field}")
    if len(repetitions_by_profile) != len(source_profiles):
        raise EvaluatorError("replay report profile count does not match sourceProfiles")
    expected_repetitions = set(range(repetitions))
    if any(values != expected_repetitions for values in repetitions_by_profile.values()):
        raise EvaluatorError("replay report has incomplete profile repetitions")


def select_profile_report(
    report: Mapping[str, Any],
    profile_id: str,
    *,
    source_file_name: str | None = None,
) -> dict[str, Any]:
    """Return a schema-valid report containing every repetition of one profile."""

    validate_report(report)
    selected = [dict(result) for result in report["results"] if result["profileId"] == profile_id]
    if not selected:
        raise EvaluatorError(f"report has no profileId: {profile_id}")
    if source_file_name is None:
        if len(report["sourceProfiles"]) != 1:
            raise EvaluatorError(
                "source_file_name is required when selecting from a multi-profile report"
            )
        source_file_name = report["sourceProfiles"][0]
    if source_file_name not in report["sourceProfiles"]:
        raise EvaluatorError("selected profile source file is absent from report")
    selected_report = {
        "schemaVersion": report["schemaVersion"],
        "createdAtEpochMillis": report["createdAtEpochMillis"],
        "candidate": dict(report["candidate"]),
        "backendRole": report["backendRole"],
        "repetitions": report["repetitions"],
        "warmupRepetitions": report["warmupRepetitions"],
        "sourceProfiles": [source_file_name],
        "results": selected,
    }
    validate_report(selected_report)
    return selected_report


def aggregate_profile_trial_reports(
    *,
    candidate: Mapping[str, Any],
    backend_role: str,
    repetitions: int,
    warmups: int,
    profile_file_names: Mapping[str, str],
    trials: Sequence[tuple[str, int, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Merge one-profile/one-repetition reports into a full workload report."""

    if repetitions <= 0:
        raise EvaluatorError("aggregate repetitions must be positive")
    if warmups < 0:
        raise EvaluatorError("aggregate warmups must not be negative")
    normalized = normalize_candidate(candidate)
    if not profile_file_names:
        raise EvaluatorError("aggregate requires at least one profile")

    expected = {
        (profile_id, repetition)
        for profile_id in profile_file_names
        for repetition in range(repetitions)
    }
    actual: set[tuple[str, int]] = set()
    merged_results: list[dict[str, Any]] = []
    created_at = 0
    for profile_id, repetition, report in trials:
        coordinate = (profile_id, repetition)
        if coordinate in actual:
            raise EvaluatorError("duplicate profile trial in aggregate")
        if coordinate not in expected:
            raise EvaluatorError("unexpected profile trial in aggregate")
        validate_report(report, expected_candidate_id=normalized["candidateId"])
        if report["candidate"] != normalized:
            raise EvaluatorError("profile trial candidate does not match aggregate candidate")
        if report["backendRole"] != backend_role:
            raise EvaluatorError("profile trial backend role mismatch")
        if report["repetitions"] != 1 or len(report["results"]) != 1:
            raise EvaluatorError("profile trial report must contain exactly one repetition")
        if report["warmupRepetitions"] != warmups:
            raise EvaluatorError("profile trial warmup policy mismatch")
        if report["sourceProfiles"] != [profile_file_names[profile_id]]:
            raise EvaluatorError("profile trial source file mismatch")
        result = dict(report["results"][0])
        if result["profileId"] != profile_id:
            raise EvaluatorError("profile trial profileId mismatch")
        result["repetition"] = repetition
        merged_results.append(result)
        created_at = max(created_at, report["createdAtEpochMillis"])
        actual.add(coordinate)

    missing = expected.difference(actual)
    if missing:
        raise EvaluatorError(f"aggregate is missing {len(missing)} profile trials")
    profile_order = list(profile_file_names)
    order = {profile_id: index for index, profile_id in enumerate(profile_order)}
    merged_results.sort(key=lambda item: (order[item["profileId"]], item["repetition"]))
    aggregate = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "createdAtEpochMillis": created_at,
        "candidate": normalized,
        "backendRole": backend_role,
        "repetitions": repetitions,
        "warmupRepetitions": warmups,
        "sourceProfiles": [profile_file_names[profile_id] for profile_id in profile_order],
        "results": merged_results,
    }
    validate_report(aggregate, expected_candidate_id=normalized["candidateId"])
    return aggregate


def validate_baseline_preflight(
    report: Mapping[str, Any],
    parameter_space: Mapping[str, Any],
    workload: Mapping[str, Any],
) -> None:
    validate_parameter_space(parameter_space)
    validate_workload_manifest(workload)
    validate_report(report)
    candidate = normalize_candidate(report["candidate"], parameter_space)
    if candidate["options"]:
        raise EvaluatorError("baseline candidate must use solver defaults")
    validate_report_against_workload(
        report,
        parameter_space,
        workload,
        expected_repetitions=report["repetitions"],
        expected_warmups=report["warmupRepetitions"],
    )
    for result in report["results"]:
        if result["outcome"] != "SUCCESS":
            raise EvaluatorError("baseline contains a failed replay")
        if result["wrongAnswerCount"]:
            raise EvaluatorError("baseline produced a wrong SAT/UNSAT answer")
        if result["knownStatusTimeoutCount"]:
            raise EvaluatorError("baseline did not reproduce a known result")
        if result["recordedFailureEventCount"]:
            raise EvaluatorError("baseline workload contains a recorded failure event")
    self_score = score_reports(report, report, exclude_recorded_failures=False)
    if not self_score["eligible"] or not math.isclose(
        float(self_score["reward"]), 0.0, abs_tol=1e-12
    ):
        raise EvaluatorError("baseline self-score is not a clean zero")


def validate_report_against_workload(
    report: Mapping[str, Any],
    parameter_space: Mapping[str, Any],
    workload: Mapping[str, Any],
    *,
    expected_repetitions: int,
    expected_warmups: int,
) -> None:
    validate_parameter_space(parameter_space)
    validate_workload_manifest(workload)
    validate_report(report)
    candidate = report["candidate"]
    if candidate["parameterSpaceId"] != parameter_space["parameterSpaceId"]:
        raise EvaluatorError("report parameterSpaceId does not match parameter space")
    unknown_options = sorted(
        set(candidate["options"]).difference(parameter_space["options"])
    )
    if unknown_options:
        raise EvaluatorError(
            "report candidate contains options outside the parameter space: "
            + ", ".join(unknown_options)
        )
    if report["backendRole"] != parameter_space["backendRole"]:
        raise EvaluatorError("report backendRole does not match parameter space")
    if report["backendRole"] != workload["backendRole"]:
        raise EvaluatorError("report backendRole does not match workload")
    if report["repetitions"] != expected_repetitions:
        raise EvaluatorError("report repetitions do not match evaluator policy")
    if report["warmupRepetitions"] != expected_warmups:
        raise EvaluatorError("report warmups do not match evaluator policy")
    expected_names = {entry["fileName"] for entry in workload["profiles"]}
    if set(report["sourceProfiles"]) != expected_names:
        raise EvaluatorError("report sourceProfiles do not match workload manifest")
    expected_by_id = {entry["profileId"]: entry for entry in workload["profiles"]}
    actual_ids = {result["profileId"] for result in report["results"]}
    if actual_ids != set(expected_by_id):
        raise EvaluatorError("report profileIds do not match workload manifest")
    compatible = set(parameter_space["compatibleSolverVersions"])
    for result in report["results"]:
        expected = expected_by_id[result["profileId"]]
        if result["runId"] != expected["runId"]:
            raise EvaluatorError("report runId does not match workload manifest")
        if result["solverVersion"] != expected["solverVersion"]:
            raise EvaluatorError("report solverVersion does not match workload profile")
        if result["solverVersion"] not in compatible:
            raise EvaluatorError("report solverVersion is incompatible with parameter space")


def score_reports(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    timeout_penalty: float = 10.0,
    reward_clip: float = 5.0,
    exclude_recorded_failures: bool = True,
) -> dict[str, Any]:
    if timeout_penalty < 1.0 or not math.isfinite(timeout_penalty):
        raise EvaluatorError("timeout penalty must be finite and at least 1")
    if reward_clip <= 0.0 or not math.isfinite(reward_clip):
        raise EvaluatorError("reward clip must be finite and positive")

    validate_report(baseline)
    validate_report(candidate)
    baseline_candidate = baseline["candidate"]
    evaluated_candidate = candidate["candidate"]
    if baseline["backendRole"] != candidate["backendRole"]:
        raise EvaluatorError("baseline and candidate use different backend roles")
    if baseline_candidate["parameterSpaceId"] != evaluated_candidate["parameterSpaceId"]:
        raise EvaluatorError("baseline and candidate use different parameter spaces")

    baseline_results = _group_by_profile(baseline["results"])
    candidate_results = _group_by_profile(candidate["results"])
    profile_scores: list[dict[str, Any]] = []
    excluded_profiles: list[dict[str, str]] = []
    eligible = True
    total_wrong_answers = 0
    total_known_timeouts = 0
    total_failed_replays = 0

    for profile_id in sorted(baseline_results):
        baseline_repetitions = baseline_results[profile_id]
        candidate_repetitions = candidate_results.get(profile_id, [])
        exclusion = _baseline_exclusion_reason(
            baseline_repetitions,
            exclude_recorded_failures=exclude_recorded_failures,
        )
        if exclusion is not None:
            excluded_profiles.append({"profileId": profile_id, "reason": exclusion})
            continue

        baseline_cost = statistics.median(
            _effective_cost(result, timeout_penalty) for result in baseline_repetitions
        )
        failed = sum(result.get("outcome") != "SUCCESS" for result in candidate_repetitions)
        wrong_answers = sum(
            _non_negative_int(result, "wrongAnswerCount")
            for result in candidate_repetitions
        )
        known_timeouts = sum(
            _non_negative_int(result, "knownStatusTimeoutCount")
            for result in candidate_repetitions
        )
        total_failed_replays += failed + int(not candidate_repetitions)
        total_wrong_answers += wrong_answers
        total_known_timeouts += known_timeouts

        hard_failure = (
            not candidate_repetitions
            or failed > 0
            or wrong_answers > 0
            or known_timeouts > 0
        )
        if hard_failure:
            eligible = False
            candidate_cost = None
            reward = -reward_clip
        else:
            candidate_cost = statistics.median(
                _effective_cost(result, timeout_penalty)
                for result in candidate_repetitions
            )
            reward = _clamp(math.log(baseline_cost / candidate_cost), reward_clip)

        profile_scores.append(
            {
                "profileId": profile_id,
                "baselineCostNanos": baseline_cost,
                "candidateCostNanos": candidate_cost,
                "reward": reward,
                "wrongAnswerCount": wrong_answers,
                "knownStatusTimeoutCount": known_timeouts,
                "failedReplayCount": failed + int(not candidate_repetitions),
            }
        )

    if not profile_scores:
        raise EvaluatorError("no comparable clean baseline profiles remain")

    extra_profiles = sorted(set(candidate_results).difference(baseline_results))
    for profile_id in extra_profiles:
        excluded_profiles.append(
            {"profileId": profile_id, "reason": "missing-from-baseline"}
        )

    return {
        "schemaVersion": SCORE_SCHEMA_VERSION,
        "baselineCandidateId": baseline_candidate["candidateId"],
        "candidateId": evaluated_candidate["candidateId"],
        "parameterSpaceId": evaluated_candidate["parameterSpaceId"],
        "backendRole": baseline["backendRole"],
        "eligible": eligible,
        "reward": statistics.fmean(item["reward"] for item in profile_scores),
        "policy": {
            "aggregation": "workload-macro-mean-log-speedup-v0",
            "objective": "non-check-plus-known-check-time-v0",
            "timeoutPenalty": timeout_penalty,
            "rewardClip": reward_clip,
            "excludeExpectedUnknownChecks": True,
            "excludeRecordedFailureProfiles": exclude_recorded_failures,
        },
        "totals": {
            "comparedProfileCount": len(profile_scores),
            "excludedProfileCount": len(excluded_profiles),
            "wrongAnswerCount": total_wrong_answers,
            "knownStatusTimeoutCount": total_known_timeouts,
            "failedReplayCount": total_failed_replays,
        },
        "profiles": profile_scores,
        "excludedProfiles": excluded_profiles,
    }


def _effective_cost(result: Mapping[str, Any], timeout_penalty: float) -> float:
    if result.get("outcome") != "SUCCESS":
        raise EvaluatorError("cannot calculate a cost for a failed replay")
    objective = _non_negative_int(result, "nonCheckDurationNanos") + _non_negative_int(
        result, "knownCheckDurationNanos"
    )
    timeout_duration = _non_negative_int(
        result, "knownStatusTimeoutDurationNanos"
    )
    timeout_budget = _non_negative_int(result, "knownStatusTimeoutBudgetNanos")
    adjusted = objective - timeout_duration + timeout_penalty * timeout_budget
    return max(float(adjusted), 1.0)


def _baseline_exclusion_reason(
    results: Sequence[Mapping[str, Any]],
    *,
    exclude_recorded_failures: bool,
) -> str | None:
    if any(result.get("outcome") != "SUCCESS" for result in results):
        raise EvaluatorError("baseline replay contains a failed result")
    if any(_non_negative_int(result, "wrongAnswerCount") for result in results):
        raise EvaluatorError("baseline replay produced a wrong SAT/UNSAT answer")
    if any(_non_negative_int(result, "knownStatusTimeoutCount") for result in results):
        raise EvaluatorError("baseline replay did not reproduce a known result")
    if exclude_recorded_failures and any(
        _non_negative_int(result, "recordedFailureEventCount") for result in results
    ):
        return "recorded-workload-failure"
    return None


def _group_by_profile(
    results: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for result in results:
        profile_id = _require_non_empty_string(result, "profileId")
        grouped[profile_id].append(result)
    return dict(grouped)


def validate_score(
    score: Mapping[str, Any], *, expected_candidate_id: str | None = None
) -> None:
    required = {
        "schemaVersion",
        "baselineCandidateId",
        "candidateId",
        "parameterSpaceId",
        "backendRole",
        "eligible",
        "reward",
        "policy",
        "totals",
        "profiles",
        "excludedProfiles",
    }
    if set(score) != required:
        raise EvaluatorError("score fields do not match the v0 contract")
    _require_equal(score, "schemaVersion", SCORE_SCHEMA_VERSION)
    _require_non_empty_string(score, "baselineCandidateId")
    candidate_id = _require_non_empty_string(score, "candidateId")
    _require_non_empty_string(score, "parameterSpaceId")
    if expected_candidate_id is not None and candidate_id != expected_candidate_id:
        raise EvaluatorError("score belongs to another candidate")
    if score.get("backendRole") not in {"regular-bv", "integer-blasted"}:
        raise EvaluatorError("score has invalid backendRole")
    if not isinstance(score.get("eligible"), bool):
        raise EvaluatorError("score eligible must be Boolean")
    reward = score.get("reward")
    if isinstance(reward, bool) or not isinstance(reward, (int, float)) or not math.isfinite(
        float(reward)
    ):
        raise EvaluatorError("score reward must be finite")
    policy = score.get("policy")
    required_policy = {
        "aggregation",
        "objective",
        "timeoutPenalty",
        "rewardClip",
        "excludeExpectedUnknownChecks",
        "excludeRecordedFailureProfiles",
    }
    if not isinstance(policy, dict) or set(policy) != required_policy:
        raise EvaluatorError("score policy fields do not match the v0 contract")
    if policy.get("aggregation") != "workload-macro-mean-log-speedup-v0":
        raise EvaluatorError("score policy has an unsupported aggregation")
    if policy.get("objective") != "non-check-plus-known-check-time-v0":
        raise EvaluatorError("score policy has an unsupported objective")
    for field, minimum, inclusive in (
        ("timeoutPenalty", 1.0, True),
        ("rewardClip", 0.0, False),
    ):
        value = policy.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or (value < minimum if inclusive else value <= minimum)
        ):
            raise EvaluatorError(f"score policy has invalid {field}")
    for field in (
        "excludeExpectedUnknownChecks",
        "excludeRecordedFailureProfiles",
    ):
        if not isinstance(policy.get(field), bool):
            raise EvaluatorError(f"score policy has invalid {field}")
    totals = score.get("totals")
    total_fields = {
        "comparedProfileCount",
        "excludedProfileCount",
        "wrongAnswerCount",
        "knownStatusTimeoutCount",
        "failedReplayCount",
    }
    if not isinstance(totals, dict) or set(totals) != total_fields:
        raise EvaluatorError("score totals fields do not match the v0 contract")
    for field in total_fields:
        _non_negative_int(totals, field)
    profiles = score.get("profiles")
    excluded = score.get("excludedProfiles")
    if not isinstance(profiles, list) or not profiles:
        raise EvaluatorError("score must contain profile results")
    if not isinstance(excluded, list):
        raise EvaluatorError("score excludedProfiles must be an array")
    if len(profiles) != totals["comparedProfileCount"]:
        raise EvaluatorError("score comparedProfileCount mismatch")
    if len(excluded) != totals["excludedProfileCount"]:
        raise EvaluatorError("score excludedProfileCount mismatch")
    profile_fields = {
        "profileId",
        "baselineCostNanos",
        "candidateCostNanos",
        "reward",
        "wrongAnswerCount",
        "knownStatusTimeoutCount",
        "failedReplayCount",
    }
    profile_ids: set[str] = set()
    profile_rewards: list[float] = []
    accumulated = {
        "wrongAnswerCount": 0,
        "knownStatusTimeoutCount": 0,
        "failedReplayCount": 0,
    }
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) != profile_fields:
            raise EvaluatorError("score profile fields do not match the v0 contract")
        profile_id = _require_non_empty_string(profile, "profileId")
        if profile_id in profile_ids:
            raise EvaluatorError("score contains a duplicate profileId")
        profile_ids.add(profile_id)
        baseline_cost = profile.get("baselineCostNanos")
        if (
            isinstance(baseline_cost, bool)
            or not isinstance(baseline_cost, (int, float))
            or not math.isfinite(float(baseline_cost))
            or baseline_cost <= 0
        ):
            raise EvaluatorError("score profile has invalid baselineCostNanos")
        candidate_cost = profile.get("candidateCostNanos")
        if candidate_cost is not None and (
            isinstance(candidate_cost, bool)
            or not isinstance(candidate_cost, (int, float))
            or not math.isfinite(float(candidate_cost))
            or candidate_cost <= 0
        ):
            raise EvaluatorError("score profile has invalid candidateCostNanos")
        profile_reward = profile.get("reward")
        if (
            isinstance(profile_reward, bool)
            or not isinstance(profile_reward, (int, float))
            or not math.isfinite(float(profile_reward))
        ):
            raise EvaluatorError("score profile has invalid reward")
        profile_rewards.append(float(profile_reward))
        for field in accumulated:
            accumulated[field] += _non_negative_int(profile, field)
        has_failure = any(profile[field] for field in accumulated)
        if (candidate_cost is None) != has_failure:
            raise EvaluatorError("score profile cost does not match its failure counters")
    if not math.isclose(
        float(reward), statistics.fmean(profile_rewards), rel_tol=1e-12, abs_tol=1e-12
    ):
        raise EvaluatorError("score reward does not match profile rewards")
    for field, value in accumulated.items():
        if totals[field] != value:
            raise EvaluatorError(f"score total does not match profiles: {field}")
    if bool(score["eligible"]) != all(value == 0 for value in accumulated.values()):
        raise EvaluatorError("score eligibility does not match failure counters")
    excluded_fields = {"profileId", "reason"}
    excluded_ids: set[str] = set()
    for item in excluded:
        if not isinstance(item, dict) or set(item) != excluded_fields:
            raise EvaluatorError("excluded score profile fields do not match the v0 contract")
        profile_id = _require_non_empty_string(item, "profileId")
        _require_non_empty_string(item, "reason")
        if profile_id in profile_ids or profile_id in excluded_ids:
            raise EvaluatorError("score contains a duplicate or overlapping profileId")
        excluded_ids.add(profile_id)


def _validate_non_negative_integer_map(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise EvaluatorError(f"{label} must be an object")
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise EvaluatorError(f"{label} has an invalid key")
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise EvaluatorError(f"{label} has an invalid value")


def _validate_mismatch(value: Any) -> None:
    if not isinstance(value, dict):
        raise EvaluatorError("mismatch must be an object")
    required = {"checkId", "expectedStatus", "actualStatus"}
    if not required.issubset(value) or set(value).difference(required | {"timeoutNanos"}):
        raise EvaluatorError("mismatch fields do not match the v0 contract")
    _non_negative_int(value, "checkId")
    if value.get("expectedStatus") not in {"SAT", "UNSAT"}:
        raise EvaluatorError("mismatch has invalid expectedStatus")
    if value.get("actualStatus") not in {"SAT", "UNSAT", "UNKNOWN"}:
        raise EvaluatorError("mismatch has invalid actualStatus")
    timeout = value.get("timeoutNanos")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 0
    ):
        raise EvaluatorError("mismatch has invalid timeoutNanos")


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_parameter_space(
    parameter_space: Mapping[str, Any], parameter_space_id: str | None = None
) -> None:
    _require_equal(parameter_space, "schemaVersion", "cvc5-parameter-space-v0")
    _require_equal(parameter_space, "targetSolver", "cvc5")
    _require_equal(parameter_space, "applicationLifecycle", "fresh-solver")
    actual_id = parameter_space.get("parameterSpaceId")
    if not isinstance(actual_id, str) or not actual_id:
        raise EvaluatorError("parameterSpaceId must be a non-empty string")
    if parameter_space_id is not None and actual_id != parameter_space_id:
        raise EvaluatorError("candidate and parameter-space ids do not match")
    if parameter_space.get("backendRole") not in {"regular-bv", "integer-blasted"}:
        raise EvaluatorError("parameter-space backendRole is invalid")
    if not isinstance(parameter_space.get("inventoryId"), str) or not parameter_space["inventoryId"]:
        raise EvaluatorError("parameter-space inventoryId must be a non-empty string")
    compatible_versions = parameter_space.get("compatibleSolverVersions")
    if (
        not isinstance(compatible_versions, list)
        or not compatible_versions
        or any(not isinstance(version, str) or not version for version in compatible_versions)
    ):
        raise EvaluatorError("compatibleSolverVersions must contain solver versions")
    if not isinstance(parameter_space.get("options"), dict) or not parameter_space["options"]:
        raise EvaluatorError("parameter-space options must be an object")
    domains = parameter_space["options"]
    for name, domain in domains.items():
        if not isinstance(name, str) or not name:
            raise EvaluatorError("parameter-space option names must not be empty")
        if not isinstance(domain, dict) or "default" not in domain:
            raise EvaluatorError(f"option {name} must define a default value")
        _validate_domain_value(name, domain["default"], domain)
        active_when = domain.get("activeWhen")
        if active_when is not None:
            _validate_predicate(active_when, domains, owner=name)
    constraints = parameter_space.get("constraints", [])
    if not isinstance(constraints, list):
        raise EvaluatorError("parameter-space constraints must be an array")
    for constraint in constraints:
        if (
            not isinstance(constraint, dict)
            or constraint.get("kind") != "forbid"
            or not isinstance(constraint.get("reason"), str)
            or not constraint["reason"]
        ):
            raise EvaluatorError("invalid parameter-space constraint")
        _validate_predicate(constraint.get("when"), domains)
    defaults = _effective_option_values(domains, {})
    for constraint in constraints:
        if _evaluate_predicate(constraint["when"], defaults):
            raise EvaluatorError("default configuration violates a constraint")


def active_option_names(
    parameter_space: Mapping[str, Any], proposed_values: Mapping[str, Any]
) -> set[str]:
    domains = parameter_space["options"]
    effective = {name: domain["default"] for name, domain in domains.items()}
    active = {name for name, domain in domains.items() if "activeWhen" not in domain}
    for name in active:
        if name in proposed_values:
            effective[name] = proposed_values[name]
    changed = True
    while changed:
        changed = False
        for name, domain in domains.items():
            if name in active:
                continue
            if _evaluate_predicate(domain["activeWhen"], effective):
                active.add(name)
                if name in proposed_values:
                    effective[name] = proposed_values[name]
                changed = True
    return active


def _effective_option_values(
    domains: Mapping[str, Any], options: Mapping[str, Any]
) -> dict[str, Any]:
    values = {name: domain["default"] for name, domain in domains.items()}
    values.update(options)
    return values


def _validate_predicate(
    predicate: Any, domains: Mapping[str, Any], owner: str | None = None
) -> None:
    if not isinstance(predicate, dict):
        raise EvaluatorError("parameter-space predicate must be an object")
    if "option" in predicate:
        option = predicate.get("option")
        if option not in domains:
            raise EvaluatorError(f"predicate references unknown option: {option}")
        if option == owner:
            raise EvaluatorError(f"option {owner} cannot activate itself")
        if set(predicate) == {"option", "equals"}:
            _validate_domain_value(option, predicate["equals"], domains[option])
            return
        if set(predicate) == {"option", "in"} and isinstance(predicate["in"], list) and predicate["in"]:
            for value in predicate["in"]:
                _validate_domain_value(option, value, domains[option])
            return
        raise EvaluatorError("invalid option predicate")
    for operator in ("all", "any"):
        if set(predicate) == {operator} and isinstance(predicate[operator], list) and predicate[operator]:
            for child in predicate[operator]:
                _validate_predicate(child, domains, owner)
            return
    if set(predicate) == {"not"}:
        _validate_predicate(predicate["not"], domains, owner)
        return
    raise EvaluatorError("unsupported parameter-space predicate")


def _evaluate_predicate(predicate: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    if "option" in predicate:
        value = values[predicate["option"]]
        if "equals" in predicate:
            return value == predicate["equals"]
        return value in predicate["in"]
    if "all" in predicate:
        return all(_evaluate_predicate(child, values) for child in predicate["all"])
    if "any" in predicate:
        return any(_evaluate_predicate(child, values) for child in predicate["any"])
    return not _evaluate_predicate(predicate["not"], values)


def _validate_domain_value(name: str, value: Any, domain: Any) -> None:
    if not isinstance(domain, dict):
        raise EvaluatorError(f"invalid domain for option {name}")
    kind = domain.get("kind")
    if kind == "boolean":
        if not isinstance(value, bool):
            raise EvaluatorError(f"option {name} must be Boolean")
        return
    if kind == "categorical":
        if not isinstance(value, str) or value not in domain.get("values", []):
            raise EvaluatorError(f"option {name} is outside its categorical domain")
        return
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise EvaluatorError(f"option {name} must be an integer")
        minimum = domain["minimum"]
        maximum = domain["maximum"]
        step = domain.get("step", 1)
        if value < minimum or value > maximum or (value - minimum) % step != 0:
            raise EvaluatorError(f"option {name} is outside its integer domain")
        return
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvaluatorError(f"option {name} must be numeric")
        numeric = float(value)
        if (
            not math.isfinite(numeric)
            or numeric < domain["minimum"]
            or numeric > domain["maximum"]
        ):
            raise EvaluatorError(f"option {name} is outside its numeric domain")
        return
    raise EvaluatorError(f"unsupported domain kind for option {name}: {kind}")


def _cvc5_option_string(name: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvaluatorError(f"option {name} must be finite")
        return format(value, ".17g")
    if isinstance(value, str) and value:
        return value
    raise EvaluatorError(f"option {name} must be a non-empty scalar value")


def _require_equal(value: Mapping[str, Any], key: str, expected: Any) -> None:
    if value.get(key) != expected:
        raise EvaluatorError(f"{key} must equal {expected!r}")


def _require_non_empty_string(value: Mapping[str, Any], key: str) -> str:
    field = value.get(key)
    if not isinstance(field, str) or not field:
        raise EvaluatorError(f"{key} must be a non-empty string")
    return field


def _non_negative_int(value: Mapping[str, Any], key: str) -> int:
    field = value.get(key)
    if isinstance(field, bool) or not isinstance(field, int) or field < 0:
        raise EvaluatorError(f"{key} must be a non-negative integer")
    return field


def _clamp(value: float, limit: float) -> float:
    return min(max(value, -limit), limit)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def add_replay_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--tsa-cli-jar", type=Path, required=True)
        command.add_argument("--candidate", type=Path, required=True)
        command.add_argument("--parameter-space", type=Path, required=True)
        command.add_argument("--profile-dir", type=Path)
        command.add_argument("--profile", type=Path, action="append", default=[])
        command.add_argument("--backend-role", default="regular-bv")
        command.add_argument("--repetitions", type=int, default=1)
        command.add_argument("--warmups", type=int, default=0)
        command.add_argument("--process-timeout-seconds", type=float)
        command.add_argument(
            "--progress-interval-seconds",
            type=int,
            default=15,
            help="print TSA replay progress every N seconds; 0 disables heartbeat",
        )
        command.add_argument("--java", default="java")
        command.add_argument(
            "--quiet-child-output",
            action="store_true",
            help="capture TSA replay output instead of streaming progress",
        )
        command.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run", help="run a candidate in an isolated TSA JVM")
    add_replay_arguments(run)

    baseline = commands.add_parser(
        "baseline", help="run and strictly qualify a default baseline"
    )
    add_replay_arguments(baseline)
    baseline.add_argument("--manifest-output", type=Path, required=True)

    score = commands.add_parser("score", help="score a replay report against a baseline")
    score.add_argument("--baseline", type=Path, required=True)
    score.add_argument("--candidate", type=Path, required=True)
    score.add_argument("--timeout-penalty", type=float, default=10.0)
    score.add_argument("--reward-clip", type=float, default=5.0)
    score.add_argument("--include-recorded-failures", action="store_true")
    score.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.command in {"run", "baseline"}:
        parameter_space = load_json(arguments.parameter_space)
        report = run_profile_replay_matrix(
            tsa_cli_jar=arguments.tsa_cli_jar,
            candidate=load_json(arguments.candidate),
            parameter_space=parameter_space,
            output_path=arguments.output,
            profile_directory=arguments.profile_dir,
            profiles=arguments.profile,
            backend_role=arguments.backend_role,
            repetitions=arguments.repetitions,
            warmups=arguments.warmups,
            java=arguments.java,
            stream_child_output=not arguments.quiet_child_output,
            progress_interval_seconds=arguments.progress_interval_seconds,
            process_timeout_seconds=arguments.process_timeout_seconds,
        )
        if arguments.command == "baseline":
            workload = describe_workload(
                profile_directory=arguments.profile_dir,
                profiles=arguments.profile,
                backend_role=arguments.backend_role,
            )
            manifest = build_baseline_manifest(
                report_path=arguments.output,
                report=report,
                parameter_space_path=arguments.parameter_space,
                parameter_space=parameter_space,
                tsa_cli_jar=arguments.tsa_cli_jar,
                workload=workload,
            )
            write_json(arguments.manifest_output, manifest)
    else:
        score = score_reports(
            load_json(arguments.baseline),
            load_json(arguments.candidate),
            timeout_penalty=arguments.timeout_penalty,
            reward_clip=arguments.reward_clip,
            exclude_recorded_failures=not arguments.include_recorded_failures,
        )
        validate_score(score)
        write_json(arguments.output, score)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
