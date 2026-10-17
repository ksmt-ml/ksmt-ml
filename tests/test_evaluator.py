import json
import math
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ksmt_ml.evaluator import (
    EvaluatorError,
    ReplayTimeoutError,
    aggregate_profile_trial_reports,
    build_baseline_manifest,
    describe_workload,
    normalize_candidate,
    run_profile_replay_matrix,
    run_replay,
    score_reports,
    select_profile_report,
    validate_baseline_manifest,
    validate_baseline_preflight,
    validate_report,
)


def candidate(candidate_id: str, options=None):
    return {
        "schemaVersion": "cvc5-replay-candidate-v0",
        "candidateId": candidate_id,
        "parameterSpaceId": "test-space-v0",
        "targetSolver": "cvc5",
        "options": options or {},
    }


def result(
    candidate_id: str,
    profile_id: str,
    *,
    non_check: int = 10,
    known_check: int = 90,
    timeout_duration: int = 0,
    timeout_budget: int = 0,
    timeout_count: int = 0,
    wrong_answers: int = 0,
    recorded_failures: int = 0,
    outcome: str = "SUCCESS",
):
    return {
        "schemaVersion": "tsa-cvc5-profile-replay-result-v0",
        "candidateId": candidate_id,
        "parameterSpaceId": "test-space-v0",
        "profileId": profile_id,
        "runId": "run-v0",
        "solverVersion": "1.3.4",
        "backendRole": "regular-bv",
        "repetition": 0,
        "outcome": outcome,
        "wallDurationNanos": non_check + known_check,
        "decodeDurationNanos": 0,
        "solverOperationDurationNanos": non_check + known_check,
        "nonCheckDurationNanos": non_check,
        "knownCheckDurationNanos": known_check,
        "unknownCheckDurationNanos": 0,
        "knownStatusTimeoutDurationNanos": timeout_duration,
        "knownStatusTimeoutBudgetNanos": timeout_budget,
        "eventCount": 3,
        "executedEventCount": 3,
        "recordedFailureEventCount": recorded_failures,
        "skippedResultAccessCount": 0,
        "checkCount": 1,
        "knownStatusMatchCount": int(timeout_count == 0 and wrong_answers == 0),
        "knownStatusTimeoutCount": timeout_count,
        "wrongAnswerCount": wrong_answers,
        "expectedUnknownCount": 0,
        "resolvedUnknownCount": 0,
        "actualStatusCounts": {"SAT": 1},
        "operationDurationNanos": {"check": known_check},
        "mismatches": [],
        "failureType": None,
        "failureMessage": None,
    }


def report(candidate_id: str, results):
    return {
        "schemaVersion": "tsa-cvc5-profile-replay-report-v0",
        "createdAtEpochMillis": 1,
        "candidate": candidate(candidate_id),
        "backendRole": "regular-bv",
        "repetitions": 1,
        "warmupRepetitions": 0,
        "sourceProfiles": [f"{item['profileId']}.json" for item in results],
        "results": results,
    }


class CandidateValidationTest(unittest.TestCase):
    def test_normalizes_cvc5_scalar_values(self):
        normalized = normalize_candidate(
            candidate("candidate-v0", {"bool-opt": True, "int-opt": 4})
        )

        self.assertEqual(normalized["options"], {"bool-opt": "true", "int-opt": "4"})

    def test_rejects_evaluator_controlled_option(self):
        with self.assertRaises(EvaluatorError):
            normalize_candidate(candidate("candidate-v0", {"tlimit-per": 10}))

    def test_validates_parameter_domain(self):
        parameter_space = {
            "schemaVersion": "cvc5-parameter-space-v0",
            "parameterSpaceId": "test-space-v0",
            "targetSolver": "cvc5",
            "compatibleSolverVersions": ["1.3.4"],
            "applicationLifecycle": "fresh-solver",
            "backendRole": "regular-bv",
            "inventoryId": "test-inventory-v0",
            "options": {
                "mode": {"kind": "categorical", "values": ["a", "b"], "default": "a"}
            },
        }

        with self.assertRaises(EvaluatorError):
            normalize_candidate(candidate("candidate-v0", {"mode": "c"}), parameter_space)


class RewardTest(unittest.TestCase):
    def test_reports_macro_log_speedup(self):
        baseline = report("default-v0", [result("default-v0", "profile-1")])
        evaluated = report(
            "candidate-v0",
            [result("candidate-v0", "profile-1", non_check=5, known_check=45)],
        )

        score = score_reports(baseline, evaluated)

        self.assertTrue(score["eligible"])
        self.assertAlmostEqual(score["reward"], math.log(2.0))

    def test_known_status_timeout_is_hard_failure(self):
        baseline = report("default-v0", [result("default-v0", "profile-1")])
        evaluated = report(
            "candidate-v0",
            [
                result(
                    "candidate-v0",
                    "profile-1",
                    non_check=10,
                    known_check=50,
                    timeout_duration=50,
                    timeout_budget=100,
                    timeout_count=1,
                )
            ],
        )

        score = score_reports(baseline, evaluated, timeout_penalty=10)

        self.assertFalse(score["eligible"])
        self.assertIsNone(score["profiles"][0]["candidateCostNanos"])
        self.assertEqual(score["reward"], -5)
        self.assertEqual(score["totals"]["knownStatusTimeoutCount"], 1)

    def test_wrong_answer_is_hard_failure(self):
        baseline = report("default-v0", [result("default-v0", "profile-1")])
        evaluated = report(
            "candidate-v0",
            [result("candidate-v0", "profile-1", wrong_answers=1)],
        )

        score = score_reports(baseline, evaluated, reward_clip=4)

        self.assertFalse(score["eligible"])
        self.assertEqual(score["reward"], -4)

    def test_excludes_profile_with_recorded_workload_failure(self):
        baseline = report(
            "default-v0",
            [
                result("default-v0", "clean"),
                result("default-v0", "dirty", recorded_failures=1),
            ],
        )
        evaluated = report(
            "candidate-v0",
            [
                result("candidate-v0", "clean"),
                result("candidate-v0", "dirty", recorded_failures=1),
            ],
        )

        score = score_reports(baseline, evaluated)

        self.assertEqual(score["totals"]["comparedProfileCount"], 1)
        self.assertEqual(score["excludedProfiles"][0]["profileId"], "dirty")


class ProfileTrialAggregationTest(unittest.TestCase):
    def test_matrix_does_not_validate_a_typed_candidate_twice(self):
        parameter_space = {
            "schemaVersion": "cvc5-parameter-space-v0",
            "parameterSpaceId": "test-space-v0",
            "targetSolver": "cvc5",
            "compatibleSolverVersions": ["1.3.4"],
            "applicationLifecycle": "fresh-solver",
            "backendRole": "regular-bv",
            "inventoryId": "test-inventory-v0",
            "options": {
                "bool-opt": {"kind": "boolean", "default": False},
            },
        }
        typed_candidate = candidate("candidate-v0", {"bool-opt": True})
        normalized_candidate = normalize_candidate(typed_candidate, parameter_space)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "schemaVersion": "ksmt-solver-profile-v0",
                        "profileId": "profile-1",
                        "runId": "run-v0",
                        "producer": {
                            "format": "tsa-solver-profile-json-v0",
                            "captureComplete": True,
                            "replayable": True,
                        },
                        "solver": {
                            "name": "cvc5",
                            "version": "1.3.4",
                            "attributes": {"backend-role": "regular-bv"},
                        },
                        "attributes": {
                            "backend-role": "regular-bv",
                            "workload-id": "workload-v0",
                        },
                        "events": [{"type": "close"}],
                    },
                    indent=2,
                )
            )
            trial_report = report(
                "candidate-v0", [result("candidate-v0", "profile-1")]
            )
            trial_report["candidate"] = normalized_candidate
            trial_report["sourceProfiles"] = [profile.name]

            with mock.patch(
                "ksmt_ml.evaluator.run_replay", return_value=trial_report
            ) as replay:
                aggregated = run_profile_replay_matrix(
                    tsa_cli_jar=root / "tsa-cli.jar",
                    candidate=typed_candidate,
                    parameter_space=parameter_space,
                    output_path=root / "report.json",
                    profiles=[profile],
                )

            self.assertIs(
                replay.call_args.kwargs["candidate"]["options"]["bool-opt"], True
            )
            self.assertEqual(aggregated["candidate"]["options"]["bool-opt"], "true")

    def test_selects_one_profile_with_all_baseline_repetitions(self):
        baseline = report(
            "default-v0",
            [result("default-v0", "first"), result("default-v0", "second")],
        )

        selected = select_profile_report(
            baseline, "second", source_file_name="second.json"
        )

        self.assertEqual(selected["sourceProfiles"], ["second.json"])
        self.assertEqual([item["profileId"] for item in selected["results"]], ["second"])

    def test_aggregates_complete_profile_repetition_matrix(self):
        trials = []
        for profile_id in ("first", "second"):
            for repetition in range(2):
                one = report(
                    "candidate-v0",
                    [result("candidate-v0", profile_id, non_check=10 + repetition)],
                )
                trials.append((profile_id, repetition, one))

        aggregated = aggregate_profile_trial_reports(
            candidate=candidate("candidate-v0"),
            backend_role="regular-bv",
            repetitions=2,
            warmups=0,
            profile_file_names={"first": "first.json", "second": "second.json"},
            trials=trials,
        )

        validate_report(aggregated, expected_candidate_id="candidate-v0")
        self.assertEqual(aggregated["sourceProfiles"], ["first.json", "second.json"])
        self.assertEqual(
            [(item["profileId"], item["repetition"]) for item in aggregated["results"]],
            [("first", 0), ("first", 1), ("second", 0), ("second", 1)],
        )

    def test_rejects_an_incomplete_trial_matrix(self):
        one = report("candidate-v0", [result("candidate-v0", "first")])
        with self.assertRaisesRegex(EvaluatorError, "missing 1 profile trials"):
            aggregate_profile_trial_reports(
                candidate=candidate("candidate-v0"),
                backend_role="regular-bv",
                repetitions=2,
                warmups=0,
                profile_file_names={"first": "first.json"},
                trials=[("first", 0, one)],
            )


class BaselinePreflightTest(unittest.TestCase):
    @staticmethod
    def _space():
        return {
            "schemaVersion": "cvc5-parameter-space-v0",
            "parameterSpaceId": "test-space-v0",
            "targetSolver": "cvc5",
            "compatibleSolverVersions": ["1.3.4"],
            "applicationLifecycle": "fresh-solver",
            "backendRole": "regular-bv",
            "inventoryId": "test-inventory-v0",
            "options": {
                "mode": {"kind": "categorical", "values": ["a", "b"], "default": "a"}
            },
        }

    @staticmethod
    def _write_profile(path: Path):
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": "ksmt-solver-profile-v0",
                    "profileId": "profile-1",
                    "runId": "run-v0",
                    "producer": {
                        "format": "tsa-solver-profile-json-v0",
                        "captureComplete": True,
                        "replayable": True,
                    },
                    "solver": {
                        "name": "cvc5",
                        "version": "1.3.4",
                        "attributes": {"backend-role": "regular-bv"},
                    },
                    "attributes": {
                        "backend-role": "regular-bv",
                        "workload-id": "workload-v0",
                    },
                    "events": [],
                },
                indent=2,
            )
            + "\n"
        )

    def test_manifest_binds_baseline_to_exact_profile_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile-1.json"
            self._write_profile(profile)
            parameter_space_path = root / "space.json"
            parameter_space_path.write_text(json.dumps(self._space()))
            jar = root / "tsa.jar"
            jar.write_bytes(b"jar-v0")
            baseline = report("default-v0", [result("default-v0", "profile-1")])
            report_path = root / "baseline.json"
            report_path.write_text(json.dumps(baseline))
            workload = describe_workload(
                profiles=[profile], backend_role="regular-bv"
            )
            manifest = build_baseline_manifest(
                report_path=report_path,
                report=baseline,
                parameter_space_path=parameter_space_path,
                parameter_space=self._space(),
                tsa_cli_jar=jar,
                workload=workload,
            )
            validate_baseline_manifest(
                manifest,
                report_path=report_path,
                report=baseline,
                parameter_space_path=parameter_space_path,
                parameter_space=self._space(),
                tsa_cli_jar=jar,
                current_workload=workload,
            )

            self._write_profile(profile)
            profile.write_text(profile.read_text() + " ")
            changed = describe_workload(profiles=[profile], backend_role="regular-bv")
            with self.assertRaisesRegex(EvaluatorError, "workload profiles differ"):
                validate_baseline_manifest(
                    manifest,
                    report_path=report_path,
                    report=baseline,
                    parameter_space_path=parameter_space_path,
                    parameter_space=self._space(),
                    tsa_cli_jar=jar,
                    current_workload=changed,
                )

    def test_report_rejects_duplicate_profile_repetition(self):
        duplicated = report(
            "candidate-v0",
            [result("candidate-v0", "profile-1"), result("candidate-v0", "profile-1")],
        )
        duplicated["sourceProfiles"] = ["profile-1.json"]
        duplicated["repetitions"] = 2
        with self.assertRaisesRegex(EvaluatorError, "duplicate profile repetition"):
            validate_report(duplicated)

    def test_preflight_rejects_a_non_default_or_incorrect_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "profile-1.json"
            self._write_profile(profile)
            workload = describe_workload(
                profiles=[profile], backend_role="regular-bv"
            )
            non_default = report(
                "default-v0", [result("default-v0", "profile-1")]
            )
            non_default["candidate"]["options"] = {"mode": "b"}
            with self.assertRaisesRegex(EvaluatorError, "solver defaults"):
                validate_baseline_preflight(non_default, self._space(), workload)

            incorrect = report(
                "default-v0",
                [result("default-v0", "profile-1", wrong_answers=1)],
            )
            with self.assertRaisesRegex(EvaluatorError, "wrong SAT/UNSAT"):
                validate_baseline_preflight(incorrect, self._space(), workload)


class RunnerTest(unittest.TestCase):
    @mock.patch("ksmt_ml.evaluator.subprocess.Popen")
    def test_streams_progress_and_forwards_interval(self, popen):
        process = popen.return_value
        process.wait.return_value = 0

        replay_report = report(
            "candidate-v0",
            [result("candidate-v0", "profile-1")],
        )
        with mock.patch("ksmt_ml.evaluator.load_json", return_value=replay_report):
            with mock.patch.object(Path, "is_file", return_value=True):
                run_replay(
                    tsa_cli_jar=Path("tsa-cli.jar"),
                    candidate=candidate("candidate-v0"),
                    output_path=Path("report.json"),
                    profiles=[Path("profile.json")],
                    progress_interval_seconds=7,
                )

        command = popen.call_args.args[0]
        interval_index = command.index("--progress-interval-seconds")
        self.assertEqual(command[interval_index + 1], "7")
        self.assertIsNone(popen.call_args.kwargs["stdout"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    @mock.patch("ksmt_ml.evaluator._terminate_process_group")
    @mock.patch("ksmt_ml.evaluator.subprocess.Popen")
    def test_process_timeout_becomes_typed_candidate_timeout(self, popen, terminate):
        process = popen.return_value
        process.wait.side_effect = subprocess.TimeoutExpired(["java"], timeout=0.001)

        with self.assertRaisesRegex(ReplayTimeoutError, "process budget"):
            run_replay(
                tsa_cli_jar=Path("tsa-cli.jar"),
                candidate=candidate("candidate-v0"),
                output_path=Path("report.json"),
                profiles=[Path("profile.json")],
                process_timeout_seconds=0.001,
                process_heartbeat_interval_seconds=0.001,
            )
        terminate.assert_called_once_with(process)


if __name__ == "__main__":
    unittest.main()
