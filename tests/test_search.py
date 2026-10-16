import json
import tempfile
import unittest
from pathlib import Path

from ksmt_ml.evaluator import normalize_candidate, score_reports, sha256_json
from ksmt_ml.search import (
    FileEvaluationCache,
    FileSearchStore,
    RandomSearchStrategy,
    SearchError,
    TrialResult,
    _claim_real_evaluation_number,
)


def parameter_space():
    return {
        "schemaVersion": "cvc5-parameter-space-v0",
        "parameterSpaceId": "search-test-v0",
        "targetSolver": "cvc5",
        "compatibleSolverVersions": ["1.3.4"],
        "applicationLifecycle": "fresh-solver",
        "backendRole": "regular-bv",
        "inventoryId": "test-inventory-v0",
        "options": {
            "bool-opt": {"kind": "boolean", "default": False},
            "cat-opt": {
                "kind": "categorical",
                "values": ["a", "b", "c"],
                "default": "a",
            },
            "int-opt": {
                "kind": "integer",
                "minimum": 2,
                "maximum": 10,
                "step": 2,
                "default": 2,
            },
            "number-opt": {
                "kind": "number",
                "minimum": 0.25,
                "maximum": 0.75,
                "default": 0.5,
            },
        },
    }


def cache_artifacts():
    candidate = {
        "schemaVersion": "cvc5-replay-candidate-v0",
        "candidateId": "candidate-v0",
        "parameterSpaceId": "search-test-v0",
        "targetSolver": "cvc5",
        "options": {},
    }
    result = {
        "schemaVersion": "tsa-cvc5-profile-replay-result-v0",
        "candidateId": "candidate-v0",
        "parameterSpaceId": "search-test-v0",
        "profileId": "profile-v0",
        "runId": "run-v0",
        "solverVersion": "1.3.4",
        "backendRole": "regular-bv",
        "repetition": 0,
        "outcome": "SUCCESS",
        "wallDurationNanos": 100,
        "decodeDurationNanos": 0,
        "solverOperationDurationNanos": 100,
        "nonCheckDurationNanos": 10,
        "knownCheckDurationNanos": 90,
        "unknownCheckDurationNanos": 0,
        "knownStatusTimeoutDurationNanos": 0,
        "knownStatusTimeoutBudgetNanos": 0,
        "eventCount": 1,
        "executedEventCount": 1,
        "recordedFailureEventCount": 0,
        "skippedResultAccessCount": 0,
        "checkCount": 1,
        "knownStatusMatchCount": 1,
        "knownStatusTimeoutCount": 0,
        "wrongAnswerCount": 0,
        "expectedUnknownCount": 0,
        "resolvedUnknownCount": 0,
        "actualStatusCounts": {"SAT": 1},
        "operationDurationNanos": {"check": 90},
        "mismatches": [],
        "failureType": None,
        "failureMessage": None,
    }
    report = {
        "schemaVersion": "tsa-cvc5-profile-replay-report-v0",
        "createdAtEpochMillis": 1,
        "candidate": candidate,
        "backendRole": "regular-bv",
        "repetitions": 1,
        "warmupRepetitions": 0,
        "sourceProfiles": ["profile-v0.json"],
        "results": [result],
    }
    return candidate, report, score_reports(report, report)


def experiment_contract(seed=0):
    profile = {
        "fileName": "profile.json",
        "path": "/tmp/profile.json",
        "sha256": "a" * 64,
        "sizeBytes": 1,
        "profileId": "profile-v0",
        "runId": "run-v0",
        "workloadId": "workload-v0",
        "solverName": "cvc5",
        "solverVersion": "1.3.4",
        "backendRole": "regular-bv",
        "profileSchemaVersion": "ksmt-solver-profile-v0",
        "producerFormat": "tsa-solver-profile-json-v0",
        "captureComplete": True,
        "replayable": True,
    }
    stable = {key: value for key, value in profile.items() if key != "path"}
    workload = {
        "schemaVersion": "cvc5-workload-manifest-v0",
        "backendRole": "regular-bv",
        "workloadSha256": sha256_json(
            {"backendRole": "regular-bv", "profiles": [stable]}
        ),
        "profiles": [profile],
    }
    return {
        "strategy": {
            "name": "random-search-v0",
            "seed": seed,
            "samplingPolicy": "independent-default-mutation-v0",
            "mutationProbability": 0.05,
        },
        "budget": {
            "maxEvaluations": 1,
            "maxWallTimeSeconds": 1.0,
            "perTrialTimeoutSeconds": 1.0,
            "maxConsecutiveDuplicates": 10,
        },
        "evaluator": {
            "version": "cvc5-real-per-profile-replay-search-v0",
            "tsaCliJar": "/tmp/tsa.jar",
            "tsaCliJarSha256": "b" * 64,
            "java": "java",
            "host": {
                "system": "Linux",
                "release": "test",
                "machine": "x86_64",
                "node": "test-host",
            },
            "backendRole": "regular-bv",
            "repetitions": 1,
            "warmups": 0,
            "progressIntervalSeconds": 0,
            "parameterSpace": "/tmp/space.json",
            "parameterSpaceSha256": "c" * 64,
            "baseline": "/tmp/baseline.json",
            "baselineSha256": "d" * 64,
            "baselineManifest": "/tmp/baseline-manifest.json",
            "baselineManifestSha256": "e" * 64,
            "workload": workload,
            "scoringPolicy": {
                "timeoutPenalty": 10.0,
                "rewardClip": 5.0,
                "excludeRecordedFailures": True,
            },
        },
        "objective": {
            "name": "workload-geometric-normalized-cost-v0",
            "direction": "minimize",
        },
    }


class RandomSearchStrategyTest(unittest.TestCase):
    def test_resumed_process_does_not_consume_another_evaluation_slot(self):
        state = {"actualEvaluationCount": 0}
        pending = {}

        self.assertEqual(_claim_real_evaluation_number(state, pending), 1)
        pending["attemptState"] = "INTERRUPTED"
        self.assertEqual(_claim_real_evaluation_number(state, pending), 1)
        self.assertEqual(state["actualEvaluationCount"], 1)

    def test_same_seed_produces_same_candidate_sequence(self):
        first = RandomSearchStrategy(parameter_space(), seed=17, mutation_probability=0.5)
        second = RandomSearchStrategy(parameter_space(), seed=17, mutation_probability=0.5)

        first_candidates = [first.ask().candidate for _ in range(10)]
        second_candidates = [second.ask().candidate for _ in range(10)]

        self.assertEqual(first_candidates, second_candidates)

    def test_different_seeds_change_sequence(self):
        first = RandomSearchStrategy(parameter_space(), seed=17, mutation_probability=0.5)
        second = RandomSearchStrategy(parameter_space(), seed=18, mutation_probability=0.5)

        self.assertNotEqual(first.ask().candidate, second.ask().candidate)

    def test_every_sample_is_valid_for_parameter_space(self):
        strategy = RandomSearchStrategy(parameter_space(), seed=3, mutation_probability=0.5)

        for _ in range(100):
            candidate = strategy.ask().candidate
            normalize_candidate(candidate, parameter_space())
            self.assertIn(candidate["options"].get("cat-opt", "a"), ["a", "b", "c"])
            self.assertEqual(candidate["options"].get("int-opt", 2) % 2, 0)

    def test_restore_continues_exact_logical_sequence(self):
        uninterrupted = RandomSearchStrategy(parameter_space(), seed=5, mutation_probability=0.5)
        expected = [uninterrupted.ask().candidate for _ in range(6)]

        interrupted = RandomSearchStrategy(parameter_space(), seed=5, mutation_probability=0.5)
        actual = [interrupted.ask().candidate for _ in range(3)]
        request = interrupted.ask()
        interrupted.tell(
            TrialResult(
                request_id=request.request_id,
                candidate_id=request.candidate["candidateId"],
                instance_id=request.instance_id,
                seed=request.seed,
                repetition=request.repetition,
                budget=request.budget,
                status="SUCCESS",
                cost=1.0,
                duration_nanos=1,
                censored=False,
                correctness="CORRECT",
                cache_hit=False,
            )
        )
        checkpoint = interrupted.snapshot()
        resumed = RandomSearchStrategy(parameter_space(), seed=5, mutation_probability=0.5)
        resumed.restore(checkpoint)
        actual.append(request.candidate)
        actual.extend(resumed.ask().candidate for _ in range(2))

        self.assertEqual(actual, expected)

    def test_invalid_text_constraints_are_rejected_explicitly(self):
        constrained = parameter_space()
        constrained["constraints"] = ["a implies b"]

        with self.assertRaisesRegex(SearchError, "invalid parameter-space constraint"):
            RandomSearchStrategy(constrained, seed=0)


class FileStoreTest(unittest.TestCase):
    def test_manifest_event_stream_and_checkpoint_survive_reopen(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_directory = Path(temporary) / "run"
            experiment = experiment_contract()
            store = FileSearchStore(run_directory)
            manifest, state, created = store.open_or_create(experiment)
            self.assertTrue(created)
            store.append_event("EXPERIMENT_CREATED", value=1)
            state["actualEvaluationCount"] = 1
            store.save_state(state)

            reopened = FileSearchStore(run_directory)
            same_manifest, same_state, created = reopened.open_or_create(experiment)
            reopened.append_event("EXPERIMENT_RESUMED", value=2)

            self.assertFalse(created)
            self.assertEqual(same_manifest, manifest)
            self.assertEqual(same_state["actualEvaluationCount"], 1)
            events = [
                json.loads(line)
                for line in (run_directory / "events.jsonl").read_text().splitlines()
            ]
            self.assertEqual([event["sequence"] for event in events], [0, 1])

    def test_manifest_mismatch_is_not_silently_resumed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileSearchStore(Path(temporary) / "run")
            store.open_or_create(experiment_contract())

            with self.assertRaisesRegex(SearchError, "manifest differs"):
                store.open_or_create(experiment_contract(seed=1))

    def test_file_cache_uses_completion_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = FileEvaluationCache(Path(temporary) / "cache")
            identity = {"candidate": "candidate-v0", "workload": "workload-v0"}
            key = cache.key(identity)
            self.assertIsNone(cache.get(key))

            candidate, report, score = cache_artifacts()
            cache.put(
                key,
                candidate=candidate,
                report=report,
                score=score,
                evaluation_identity=identity,
            )

            cached = cache.get(key)
            self.assertEqual(cached["candidate"]["candidateId"], "candidate-v0")
            self.assertEqual(cached["score"]["reward"], 0.0)

            score_path = Path(temporary) / "cache" / key[:2] / key / "score.json"
            score_path.write_text(score_path.read_text() + " ")
            with self.assertRaisesRegex(SearchError, "digest mismatch"):
                cache.get(key)

    def test_reopen_repairs_only_a_truncated_final_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_directory = Path(temporary) / "run"
            experiment = experiment_contract()
            store = FileSearchStore(run_directory)
            store.open_or_create(experiment)
            store.append_event("EXPERIMENT_CREATED")
            with (run_directory / "events.jsonl").open("ab") as output:
                output.write(b'{"schemaVersion":"cvc5-search-event-v0"')

            reopened = FileSearchStore(run_directory)
            reopened.open_or_create(experiment)
            reopened.append_event("EXPERIMENT_RESUMED")
            events = [
                json.loads(line)
                for line in (run_directory / "events.jsonl").read_text().splitlines()
            ]
            self.assertEqual([event["sequence"] for event in events], [0, 1])

    def test_reopen_rejects_a_corrupt_strategy_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_directory = Path(temporary) / "run"
            experiment = experiment_contract()
            store = FileSearchStore(run_directory)
            _, state, _ = store.open_or_create(experiment)
            state["strategyState"] = {
                "schemaVersion": "cvc5-random-search-state-v0",
                "strategy": "random-search-v0",
                "seed": 0,
                "samplingPolicy": "independent-default-mutation-v0",
                "mutationProbability": 0.05,
                "parameterSpaceSha256": "f" * 64,
                "instanceId": "whole-workload-v0",
                "evaluationSeed": 0,
                "repetition": 0,
                "budget": None,
                "nextProposalIndex": 0,
                "observationCount": 1,
            }
            (run_directory / "state.json").write_text(json.dumps(state))

            with self.assertRaisesRegex(SearchError, "observations exceed"):
                FileSearchStore(run_directory).open_or_create(experiment)


if __name__ == "__main__":
    unittest.main()
