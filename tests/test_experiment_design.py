import json
import io
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from ksmt_ml.confirmation_runner import (
    _candidate_for_trial,
    load_candidate_index,
    run_confirmation_schedule,
)
from ksmt_ml.evaluator import sha256_file, sha256_json
from ksmt_ml.experiment_design import (
    ExperimentDesignError,
    build_confirmation_schedule,
    build_configurator_campaign,
    build_workload_corpus,
    summarize_default_noise_calibration,
    summarize_grouped_confirmation,
    validate_configurator_campaign,
    validate_confirmation_schedule,
    validate_workload_corpus,
)
from ksmt_ml.multi_seed_campaign import (
    collect_finalists,
    initialize_campaign_workspace,
    prepare_confirmation,
    summarize_anytime,
)


def candidate(candidate_id: str) -> dict:
    return {
        "schemaVersion": "cvc5-replay-candidate-v0",
        "candidateId": candidate_id,
        "parameterSpaceId": "space-v0",
        "targetSolver": "cvc5",
        "options": {},
    }


def workload(group_id: str, solver_version: str = "1.3.4") -> dict:
    entry = {
        "fileName": f"{group_id}.json",
        "path": f"/tmp/{group_id}.json",
        "sha256": "1" * 64,
        "sizeBytes": 1,
        "profileId": f"profile-{group_id}",
        "runId": f"run-{group_id}",
        "workloadId": f"workload-{group_id}",
        "solverName": "cvc5",
        "solverVersion": solver_version,
        "backendRole": "regular-bv",
        "profileSchemaVersion": "ksmt-solver-profile-v0",
        "producerFormat": "tsa-solver-profile-json-v0",
        "captureComplete": True,
        "replayable": True,
    }
    stable_entry = {key: value for key, value in entry.items() if key != "path"}
    return {
        "schemaVersion": "cvc5-workload-manifest-v0",
        "backendRole": "regular-bv",
        "workloadSha256": sha256_json(
            {"backendRole": "regular-bv", "profiles": [stable_entry]}
        ),
        "profiles": [entry],
    }


def write_baseline(
    root: Path,
    group_id: str,
    parameter_space_path: Path | None = None,
    tsa_cli_jar: Path | None = None,
) -> Path:
    path = root / f"{group_id}-baseline.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "cvc5-baseline-manifest-v0",
                "parameterSpace": {
                    "parameterSpaceId": "space-v0",
                    "fileName": parameter_space_path.name if parameter_space_path else "unused.json",
                    "sha256": sha256_file(parameter_space_path)
                    if parameter_space_path
                    else "3" * 64,
                },
                "tsaCliJar": {
                    "fileName": tsa_cli_jar.name if tsa_cli_jar else "unused.jar",
                    "sha256": sha256_file(tsa_cli_jar) if tsa_cli_jar else "4" * 64,
                },
                "defaultCandidate": {
                    "schemaVersion": "cvc5-replay-candidate-v0",
                    "candidateId": "default-v0",
                    "parameterSpaceId": "space-v0",
                    "targetSolver": "cvc5",
                    "options": {},
                },
                "evaluationPolicy": {"timeoutPenalty": 10.0},
                "workload": workload(group_id),
            }
        ),
        encoding="utf-8",
    )
    return path


def write_corpus(
    root: Path,
    parameter_space_path: Path | None = None,
    tsa_cli_jar: Path | None = None,
) -> tuple[Path, dict]:
    groups = []
    for group_id, partition in (
        ("dev-a", "development"),
        ("validation-a", "validation"),
        ("test-a", "test"),
        ("test-b", "test"),
    ):
        baseline = write_baseline(
            root,
            group_id,
            parameter_space_path=parameter_space_path,
            tsa_cli_jar=tsa_cli_jar,
        )
        groups.append(
            {
                "groupId": group_id,
                "leakageGroupId": f"family-{group_id}",
                "partition": partition,
                "sealedUntilFinalEvaluation": partition == "test",
                "baselineManifest": {
                    "path": baseline.name,
                    "sha256": sha256_file(baseline),
                },
            }
        )
    corpus = {
        "schemaVersion": "cvc5-workload-corpus-v0",
        "corpusId": "corpus-v0",
        "targetSolver": "cvc5",
        "solverVersion": "1.3.4",
        "parameterSpaceId": "space-v0",
        "groupingKey": "contract-family-and-revision-v0",
        "groups": groups,
    }
    path = root / "corpus.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")
    return path, corpus


def campaign(corpus_path: Path) -> dict:
    return {
        "schemaVersion": "cvc5-configurator-campaign-v0",
        "campaignId": "campaign-v0",
        "corpus": {
            "path": corpus_path.name,
            "sha256": sha256_file(corpus_path),
            "corpusId": "corpus-v0",
        },
        "defaultCandidateId": "default-v0",
        "objective": "workload-macro-mean-log-speedup-v0",
        "algorithms": [
            {"name": "random", "seeds": [17, 23]},
            {"name": "evolution", "seeds": [17, 23]},
            {"name": "smac", "seeds": [17, 23]},
        ],
        "budget": {
            "wallClockSeconds": 1800,
            "maximumEvaluations": 100,
            "perTrialTimeoutSeconds": 600,
        },
        "confirmation": {
            "repetitions": 3,
            "scheduleSeed": 29,
            "orderPolicy": "paired-randomized-interleaved-v0",
        },
        "gate": {
            "minimumRelativeImprovement": 0.05,
            "minimumPositiveGroupFraction": 0.60,
            "requirePositiveBootstrap95LowerBound": True,
            "requireFailureFree": True,
        },
    }


def write_finished_search_run(
    run_directory: Path,
    algorithm: str,
    seed: int,
    cost: float,
    development_baseline_sha256: str,
) -> None:
    run_directory.mkdir(parents=True, exist_ok=True)
    names = {
        "random": "random-search-v0",
        "evolution": "evolution-search-v0",
        "smac": "smac3-algorithm-configuration-v0",
    }
    candidate_value = candidate(f"{algorithm}-seed{seed}-v0")
    budget = {
        "maxWallTimeSeconds": 1800.0,
        "perTrialTimeoutSeconds": 600.0,
        "maxTrials" if algorithm == "smac" else "maxEvaluations": 100,
    }
    manifest = {
        "experiment": {
            "budget": budget,
            "evaluator": {
                "baselineManifestSha256": development_baseline_sha256,
                "scoringPolicy": {
                    "aggregation": "workload-macro-mean-log-speedup-v0"
                },
                "workload": {
                    "profiles": [{"profileId": "profile-dev-a"}],
                },
            },
            "strategy": {"name": names[algorithm], "seed": seed},
        }
    }
    if algorithm == "smac":
        incumbent = {"candidate": candidate_value, "cost": cost}
        state_schema = "cvc5-smac-search-state-v0"
        event_schema = "cvc5-smac-search-event-v0"
        incumbent_payload = {"incumbent": incumbent}
    else:
        trial_id = f"trial-{algorithm}-{seed}"
        candidate_path = run_directory / "trials" / trial_id / "candidate.json"
        candidate_path.parent.mkdir(parents=True)
        candidate_path.write_text(json.dumps(candidate_value), encoding="utf-8")
        incumbent = {
            "candidateId": candidate_value["candidateId"],
            "cost": cost,
            "trialId": trial_id,
        }
        state_schema = "cvc5-search-state-v0"
        event_schema = "cvc5-search-event-v0"
        incumbent_payload = incumbent
    state = {
        "schemaVersion": state_schema,
        "status": "FINISHED",
        "activeWallTimeNanos": 10_000_000_000,
        "completedTrialCount": 1,
        "incumbent": incumbent,
    }
    trial_result = (
        {
            "candidate_id": candidate_value["candidateId"],
            "instance_id": "profile-dev-a",
            "status": "SUCCESS",
            "correctness": "CORRECT",
            "cost": cost,
        }
        if algorithm == "smac"
        else None
    )
    events = [
        {
            "schemaVersion": event_schema,
            "sequence": 0,
            "atEpochMillis": 1_000,
            "type": "EXPERIMENT_CREATED",
            "payload": {},
        },
        {
            "schemaVersion": event_schema,
            "sequence": 1,
            "atEpochMillis": 2_000,
            "type": "TRIAL_FINISHED",
            "payload": {"result": trial_result} if trial_result else {},
        },
        {
            "schemaVersion": event_schema,
            "sequence": 2,
            "atEpochMillis": 2_000,
            "type": "INCUMBENT_CHANGED",
            "payload": incumbent_payload,
        },
        {
            "schemaVersion": event_schema,
            "sequence": 3,
            "atEpochMillis": 11_000,
            "type": "EXPERIMENT_FINISHED",
            "payload": {},
        },
    ]
    (run_directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (run_directory / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


class ExperimentDesignTest(unittest.TestCase):
    def test_confirmation_candidate_keeps_typed_boolean_until_replay(self):
        parameter_space = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "configs"
                / "cvc5-regular-bv-parameter-space-v0.json"
            ).read_text(encoding="utf-8")
        )
        selected = {
            "schemaVersion": "cvc5-replay-candidate-v0",
            "candidateId": "random-seed17-v0",
            "parameterSpaceId": parameter_space["parameterSpaceId"],
            "targetSolver": "cvc5",
            "options": {"on-repeat-ite-simp": True},
        }

        prepared = _candidate_for_trial(
            trial={
                "configurationRole": "candidate",
                "candidateId": "random-seed17-v0",
            },
            baseline_manifest={"defaultCandidate": selected},
            candidates={"random-seed17-v0": selected},
            default_controls=set(),
            parameter_space=parameter_space,
        )

        self.assertIs(prepared["options"]["on-repeat-ite-simp"], True)

    def test_builds_equal_budget_campaign_from_corpus(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_path, _ = write_corpus(root)
            output = root / "design" / "campaign-v0.json"
            output.parent.mkdir()

            value = build_configurator_campaign(
                campaign_id="evidence-v0",
                corpus_path=corpus_path,
                default_candidate_id="default-v0",
                seeds=[17, 23, 31],
                wall_clock_seconds=1800,
                maximum_evaluations=100,
                per_trial_timeout_seconds=600,
                confirmation_repetitions=3,
                confirmation_seed=29,
                minimum_relative_improvement=0.05,
                minimum_positive_group_fraction=0.6,
                output_path=output,
                objective="workload-total-effective-cost-log-speedup-v0",
            )

            self.assertEqual(value["schemaVersion"], "cvc5-configurator-campaign-v0")
            self.assertEqual(
                value["objective"],
                "workload-total-effective-cost-log-speedup-v0",
            )
            self.assertEqual(
                {tuple(item["seeds"]) for item in value["algorithms"]},
                {(17, 23, 31)},
            )
            validate_configurator_campaign(value, base_directory=output.parent)

    def test_builds_portable_corpus_from_real_baseline_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baselines = {
                name: write_baseline(root, name)
                for name in ("dev-a", "validation-a", "test-a")
            }
            output = root / "design" / "corpus.json"

            value = build_workload_corpus(
                corpus_id="corpus-v0",
                solver_version="1.3.4",
                parameter_space_id="space-v0",
                grouping_key="contract-family-and-revision-v0",
                groups=[
                    ("development", "dev-a", "family-dev", baselines["dev-a"]),
                    (
                        "validation",
                        "validation-a",
                        "family-validation",
                        baselines["validation-a"],
                    ),
                    ("test", "test-a", "family-test", baselines["test-a"]),
                ],
                output_path=output,
            )

            self.assertTrue(output.is_file())
            self.assertEqual(value["groups"][2]["partition"], "test")
            self.assertFalse(
                Path(value["groups"][0]["baselineManifest"]["path"]).is_absolute()
            )
            validate_workload_corpus(value, base_directory=output.parent)

    def test_validates_corpus_bindings_and_equal_seed_campaign(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_path, corpus = write_corpus(root)

            resolved = validate_workload_corpus(corpus, base_directory=root)
            self.assertEqual(set(resolved), {"dev-a", "validation-a", "test-a", "test-b"})
            loaded = validate_configurator_campaign(
                campaign(corpus_path), base_directory=root
            )
            self.assertEqual(loaded["corpusId"], "corpus-v0")

    def test_rejects_group_leakage_across_partitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, corpus = write_corpus(root)
            corpus["groups"][0]["leakageGroupId"] = "shared-family"
            corpus["groups"][2]["leakageGroupId"] = "shared-family"

            with self.assertRaisesRegex(ExperimentDesignError, "crosses partitions"):
                validate_workload_corpus(corpus, base_directory=root)

    def test_rejects_different_seed_sets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_path, _ = write_corpus(root)
            value = campaign(corpus_path)
            value["algorithms"][2]["seeds"] = [17, 31]

            with self.assertRaisesRegex(ExperimentDesignError, "same ordered seed set"):
                validate_configurator_campaign(value, base_directory=root)

    def test_confirmation_schedule_is_deterministic_paired_and_interleaved(self):
        arguments = {
            "campaign_id": "campaign-v0",
            "default_candidate_id": "default-v0",
            "workload_group_ids": ["test-a", "test-b"],
            "candidate_ids": ["random-v0", "smac-v0"],
            "repetitions": 3,
            "seed": 29,
        }
        first = build_confirmation_schedule(**arguments)
        second = build_confirmation_schedule(**arguments)
        other_seed = build_confirmation_schedule(**{**arguments, "seed": 31})

        self.assertEqual(first, second)
        self.assertNotEqual(first["trials"], other_seed["trials"])
        self.assertEqual(len(first["trials"]), 24)
        for index in range(0, len(first["trials"]), 2):
            pair = first["trials"][index : index + 2]
            self.assertEqual(pair[0]["pairId"], pair[1]["pairId"])
            self.assertEqual(
                {item["configurationRole"] for item in pair},
                {"default", "candidate"},
            )
        validate_confirmation_schedule(first)

        tampered = json.loads(json.dumps(first))
        tampered["trials"][0]["repetition"] = 99
        with self.assertRaisesRegex(ExperimentDesignError, "digest mismatch"):
            validate_confirmation_schedule(tampered)

    def test_balanced_confirmation_schedule_has_equal_role_order(self):
        schedule = build_confirmation_schedule(
            campaign_id="campaign-v0",
            default_candidate_id="default-v0",
            workload_group_ids=["validation-a", "validation-b"],
            candidate_ids=["default-control-v0"],
            default_control_ids=["default-control-v0"],
            repetitions=12,
            seed=29,
            balanced_role_order=True,
        )

        self.assertEqual(
            schedule["policy"], "paired-balanced-randomized-interleaved-v0"
        )
        for group_id in schedule["workloadGroupIds"]:
            pairs = [
                schedule["trials"][index : index + 2]
                for index in range(0, len(schedule["trials"]), 2)
                if schedule["trials"][index]["workloadGroupId"] == group_id
            ]
            self.assertEqual(
                sum(pair[0]["configurationRole"] == "candidate" for pair in pairs),
                6,
            )
        validate_confirmation_schedule(schedule)

        with self.assertRaisesRegex(ExperimentDesignError, "even repetition"):
            build_confirmation_schedule(
                campaign_id="campaign-v0",
                default_candidate_id="default-v0",
                workload_group_ids=["validation-a"],
                candidate_ids=["default-control-v0"],
                default_control_ids=["default-control-v0"],
                repetitions=3,
                seed=29,
                balanced_role_order=True,
            )

    def test_default_noise_summary_bootstraps_pairs_and_checks_three_timers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            schedule = build_confirmation_schedule(
                campaign_id="campaign-v0",
                default_candidate_id="default-v0",
                workload_group_ids=["validation-a"],
                candidate_ids=["default-control-v0"],
                default_control_ids=["default-control-v0"],
                repetitions=12,
                seed=29,
                balanced_role_order=True,
            )
            results = []
            seen_pairs = set()
            for trial in schedule["trials"]:
                if trial["pairId"] in seen_pairs:
                    continue
                seen_pairs.add(trial["pairId"])
                log_speedup = 0.005 if trial["repetition"] % 2 == 0 else -0.005
                default_duration = 1_000_000_000
                candidate_duration = round(default_duration / math.exp(log_speedup))
                bindings = {}
                for role, duration in (
                    ("default", default_duration),
                    ("candidate", candidate_duration),
                ):
                    report_path = root / f"{trial['pairId']}-{role}.json"
                    report_path.write_text(
                        json.dumps(
                            {
                                "schemaVersion": "tsa-cvc5-profile-replay-report-v0",
                                "results": [
                                    {
                                        "profileId": "profile-validation-a",
                                        "repetition": 0,
                                        "solverOperationDurationNanos": duration,
                                        "wallDurationNanos": duration,
                                    }
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    bindings[role] = {
                        "path": report_path.name,
                        "sha256": sha256_file(report_path),
                    }
                results.append(
                    {
                        "pairId": trial["pairId"],
                        "candidateId": trial["candidateId"],
                        "workloadGroupId": trial["workloadGroupId"],
                        "repetition": trial["repetition"],
                        "defaultReport": bindings["default"],
                        "candidateReport": bindings["candidate"],
                        "reward": log_speedup,
                        "eligible": True,
                        "wrongAnswerCount": 0,
                        "timeoutCount": 0,
                        "failureCount": 0,
                    }
                )
            observations = {
                "schemaVersion": "cvc5-confirmation-observations-v0",
                "scheduleSha256": schedule["scheduleSha256"],
                "scoreAggregation": "workload-total-effective-cost-log-speedup-v0",
                "complete": True,
                "results": results,
            }

            summary = summarize_default_noise_calibration(
                schedule,
                observations,
                target_minimum_relative_improvement=0.05,
                score_aggregation="workload-total-effective-cost-log-speedup-v0",
                bootstrap_seed=37,
                bootstrap_samples=1000,
                artifact_directory=root,
            )

            self.assertEqual(
                summary["schemaVersion"],
                "cvc5-default-noise-calibration-summary-v0",
            )
            self.assertEqual(summary["pairedRepetitionCount"], 12)
            self.assertTrue(summary["gate"]["passed"])
            self.assertEqual(summary["gate"]["failedChecks"], [])
            self.assertEqual(
                set(summary["metrics"]),
                {
                    "replayProxyLogSpeedup",
                    "solverOperationLogSpeedup",
                    "wallLogSpeedup",
                },
            )
            for metric in summary["metrics"].values():
                self.assertTrue(metric["intervalContainsZero"])
                self.assertEqual(len(metric["pairedValues"]), 12)

    def test_grouped_summary_uses_matched_repetitions_then_independent_groups(self):
        schedule = build_confirmation_schedule(
            campaign_id="campaign-v0",
            default_candidate_id="default-v0",
            workload_group_ids=["test-a", "test-b"],
            candidate_ids=["candidate-v0", "default-control-v0"],
            default_control_ids=["default-control-v0"],
            repetitions=3,
            seed=29,
        )
        results = []
        candidate_ratios = {
            ("candidate-v0", "test-a"): 2.0,
            ("candidate-v0", "test-b"): 1.25,
            ("default-control-v0", "test-a"): 1.0,
            ("default-control-v0", "test-b"): 1.0,
        }
        seen_pairs = set()
        for trial in schedule["trials"]:
            if trial["pairId"] in seen_pairs:
                continue
            seen_pairs.add(trial["pairId"])
            ratio = candidate_ratios[(trial["candidateId"], trial["workloadGroupId"])]
            results.append(
                {
                    "pairId": trial["pairId"],
                    "candidateId": trial["candidateId"],
                    "workloadGroupId": trial["workloadGroupId"],
                    "repetition": trial["repetition"],
                    "defaultReport": {
                        "path": f"{trial['pairId']}-default.json",
                        "sha256": "1" * 64,
                    },
                    "candidateReport": {
                        "path": f"{trial['pairId']}-candidate.json",
                        "sha256": "2" * 64,
                    },
                    "reward": math.log(ratio),
                    "eligible": True,
                    "wrongAnswerCount": 0,
                    "timeoutCount": 0,
                    "failureCount": 0,
                }
            )
        observations = {
            "schemaVersion": "cvc5-confirmation-observations-v0",
            "scheduleSha256": schedule["scheduleSha256"],
            "scoreAggregation": "workload-macro-mean-log-speedup-v0",
            "complete": True,
            "results": results,
        }

        summary = summarize_grouped_confirmation(
            schedule, observations, bootstrap_seed=37, bootstrap_samples=1000
        )
        by_candidate = {item["candidateId"]: item for item in summary["results"]}
        candidate_result = by_candidate["candidate-v0"]
        self.assertEqual(candidate_result["workloadGroupCount"], 2)
        self.assertEqual(candidate_result["pairedRepetitionCount"], 6)
        self.assertAlmostEqual(
            candidate_result["meanLogSpeedup"],
            (math.log(2.0) + math.log(1.25)) / 2,
        )
        self.assertEqual(candidate_result["positiveGroupCount"], 2)
        self.assertFalse(candidate_result["defaultControl"])
        self.assertAlmostEqual(by_candidate["default-control-v0"]["meanLogSpeedup"], 0)
        self.assertTrue(by_candidate["default-control-v0"]["defaultControl"])

    def test_grouped_summary_rejects_missing_trial(self):
        schedule = build_confirmation_schedule(
            campaign_id="campaign-v0",
            default_candidate_id="default-v0",
            workload_group_ids=["test-a"],
            candidate_ids=["candidate-v0"],
            repetitions=2,
            seed=1,
        )
        observations = {
            "schemaVersion": "cvc5-confirmation-observations-v0",
            "scheduleSha256": schedule["scheduleSha256"],
            "scoreAggregation": "workload-macro-mean-log-speedup-v0",
            "complete": True,
            "results": [],
        }
        with self.assertRaisesRegex(ExperimentDesignError, "do not match schedule"):
            summarize_grouped_confirmation(
                schedule, observations, bootstrap_seed=1, bootstrap_samples=100
            )

    def test_confirmation_runner_executes_frozen_order_and_persists_pairs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parameter_space = root / "parameter-space.json"
            parameter_space.write_text(
                json.dumps({"parameterSpaceId": "space-v0"}), encoding="utf-8"
            )
            tsa_jar = root / "tsa.jar"
            tsa_jar.write_bytes(b"jar")
            corpus_path, _ = write_corpus(
                root,
                parameter_space_path=parameter_space,
                tsa_cli_jar=tsa_jar,
            )
            schedule = build_confirmation_schedule(
                campaign_id="campaign-v0",
                default_candidate_id="default-v0",
                workload_group_ids=["test-a", "test-b"],
                candidate_ids=["default-control-v0"],
                default_control_ids=["default-control-v0"],
                repetitions=2,
                seed=41,
            )
            schedule_path = root / "schedule.json"
            schedule_path.write_text(json.dumps(schedule), encoding="utf-8")
            executed = []

            with self.assertRaisesRegex(
                ExperimentDesignError, "exactly the validation groups"
            ):
                run_confirmation_schedule(
                    schedule_path=schedule_path,
                    corpus_path=corpus_path,
                    parameter_space_path=parameter_space,
                    tsa_cli_jar=tsa_jar,
                    candidate_paths={},
                    output_directory=root / "wrong-partition",
                    expected_partition="validation",
                )

            def fake_trial(**arguments):
                executed.append(arguments["trial"]["trialId"])
                report = {"candidate": arguments["candidate"]}
                arguments["output_path"].parent.mkdir(parents=True, exist_ok=True)
                arguments["output_path"].write_text(json.dumps(report), encoding="utf-8")
                return report

            fake_score = {
                "reward": 0.0,
                "eligible": True,
                "totals": {
                    "wrongAnswerCount": 0,
                    "knownStatusTimeoutCount": 0,
                    "failedReplayCount": 0,
                },
            }
            with mock.patch(
                "ksmt_ml.confirmation_runner.normalize_candidate",
                side_effect=lambda candidate, _: dict(candidate),
            ), mock.patch(
                "ksmt_ml.confirmation_runner._run_or_reuse_trial",
                side_effect=fake_trial,
            ), mock.patch(
                "ksmt_ml.confirmation_runner.score_reports",
                return_value=fake_score,
            ):
                with redirect_stdout(io.StringIO()):
                    observations = run_confirmation_schedule(
                        schedule_path=schedule_path,
                        corpus_path=corpus_path,
                        parameter_space_path=parameter_space,
                        tsa_cli_jar=tsa_jar,
                        candidate_paths={},
                        output_directory=root / "confirmation",
                    )

            self.assertEqual(
                executed, [trial["trialId"] for trial in schedule["trials"]]
            )
            self.assertTrue(observations["complete"])
            self.assertEqual(len(observations["results"]), 4)
            self.assertTrue(
                (root / "confirmation" / "observations-v0.json").is_file()
            )

    def test_materializes_multi_seed_workspace_collects_every_seed_and_anytime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_path, _ = write_corpus(root)
            campaign_path = root / "campaign.json"
            campaign_path.write_text(
                json.dumps(campaign(corpus_path)), encoding="utf-8"
            )
            workspace_root = root / "workspace"

            workspace = initialize_campaign_workspace(campaign_path, workspace_root)
            self.assertEqual(len(workspace["runSlots"]), 6)
            self.assertEqual(
                initialize_campaign_workspace(campaign_path, workspace_root), workspace
            )
            materialized_corpus = json.loads(
                (workspace_root / "design" / "corpus-v0.json").read_text(
                    encoding="utf-8"
                )
            )
            development_baseline_sha256 = next(
                group["baselineManifest"]["sha256"]
                for group in materialized_corpus["groups"]
                if group["partition"] == "development"
            )
            for slot in workspace["runSlots"]:
                write_finished_search_run(
                    workspace_root / slot["path"],
                    slot["algorithm"],
                    slot["seed"],
                    0.8 if slot["seed"] == 17 else 0.9,
                    development_baseline_sha256,
                )

            invalid_manifest_path = (
                workspace_root / "runs" / "random" / "seed-17" / "manifest.json"
            )
            valid_manifest = json.loads(
                invalid_manifest_path.read_text(encoding="utf-8")
            )
            invalid_manifest = json.loads(json.dumps(valid_manifest))
            invalid_manifest["experiment"]["budget"]["maxWallTimeSeconds"] = 1799.0
            invalid_manifest_path.write_text(
                json.dumps(invalid_manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ExperimentDesignError, "budget differs from the campaign"
            ):
                collect_finalists(workspace_root)
            invalid_manifest_path.write_text(
                json.dumps(valid_manifest), encoding="utf-8"
            )

            finalists = collect_finalists(workspace_root)
            self.assertEqual(len(finalists["finalists"]), 6)
            self.assertEqual(len(finalists["slotResults"]), 6)
            self.assertTrue(
                all(
                    item["status"] == "QUALIFIED"
                    for item in finalists["slotResults"]
                )
            )
            self.assertEqual(
                {(item["algorithm"], item["seed"]) for item in finalists["finalists"]},
                {
                    (algorithm, seed)
                    for algorithm in ("random", "evolution", "smac")
                    for seed in (17, 23)
                },
            )

            schedule, candidate_index = prepare_confirmation(workspace_root)
            self.assertEqual(len(candidate_index["candidates"]), 6)
            self.assertEqual(len(schedule["trials"]), 72)
            loaded_candidates = load_candidate_index(
                workspace_root / "confirmation" / "candidate-index-v0.json",
                expected_schedule_sha256=schedule["scheduleSha256"],
            )
            self.assertEqual(
                set(loaded_candidates),
                {
                    f"{algorithm}-seed{seed}-v0"
                    for algorithm in ("random", "evolution", "smac")
                    for seed in (17, 23)
                },
            )

            anytime = summarize_anytime(workspace_root)
            self.assertEqual(len(anytime["algorithms"]), 3)
            for algorithm in anytime["algorithms"]:
                self.assertEqual(algorithm["seedCount"], 2)
                self.assertEqual(
                    algorithm["aggregate"][0]["medianGeometricSpeedup"], 1.0
                )
                self.assertGreater(
                    algorithm["aggregate"][-1]["medianGeometricSpeedup"], 1.0
                )
                self.assertTrue(
                    all(
                        run["normalizedAreaUnderSpeedupCurve"] > 1.0
                        for run in algorithm["runs"]
                    )
                )

    def test_records_unqualified_slots_and_confirms_only_eligible_incumbents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_path, _ = write_corpus(root)
            campaign_path = root / "campaign.json"
            campaign_path.write_text(
                json.dumps(campaign(corpus_path)), encoding="utf-8"
            )
            workspace_root = root / "workspace"
            workspace = initialize_campaign_workspace(campaign_path, workspace_root)
            materialized_corpus = json.loads(
                (workspace_root / "design" / "corpus-v0.json").read_text(
                    encoding="utf-8"
                )
            )
            development_baseline_sha256 = next(
                group["baselineManifest"]["sha256"]
                for group in materialized_corpus["groups"]
                if group["partition"] == "development"
            )
            for slot in workspace["runSlots"]:
                write_finished_search_run(
                    workspace_root / slot["path"],
                    slot["algorithm"],
                    slot["seed"],
                    0.8,
                    development_baseline_sha256,
                )

            for algorithm, seed in (
                ("random", 23),
                ("evolution", 17),
                ("evolution", 23),
            ):
                run = workspace_root / "runs" / algorithm / f"seed-{seed}"
                state = json.loads((run / "state.json").read_text(encoding="utf-8"))
                state["incumbent"] = None
                (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
                events = [
                    json.loads(line)
                    for line in (run / "events.jsonl").read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if json.loads(line)["type"] != "INCUMBENT_CHANGED"
                ]
                for sequence, event in enumerate(events):
                    event["sequence"] = sequence
                (run / "events.jsonl").write_text(
                    "".join(json.dumps(event) + "\n" for event in events),
                    encoding="utf-8",
                )

            for seed in (17, 23):
                run = workspace_root / "runs" / "smac" / f"seed-{seed}"
                events = [
                    json.loads(line)
                    for line in (run / "events.jsonl").read_text(
                        encoding="utf-8"
                    ).splitlines()
                ]
                trial = next(
                    event for event in events if event["type"] == "TRIAL_FINISHED"
                )
                trial["payload"]["result"].update(
                    {
                        "status": "KNOWN_STATUS_TIMEOUT",
                        "correctness": "NOT_ESTABLISHED",
                        "cost": None,
                    }
                )
                (run / "events.jsonl").write_text(
                    "".join(json.dumps(event) + "\n" for event in events),
                    encoding="utf-8",
                )

            index = collect_finalists(workspace_root)
            self.assertEqual(
                [(item["algorithm"], item["seed"]) for item in index["finalists"]],
                [("random", 17)],
            )
            self.assertEqual(len(index["slotResults"]), 6)
            self.assertEqual(
                sum(item["status"] == "NO_FEASIBLE_CANDIDATE" for item in index["slotResults"]),
                5,
            )

            schedule, candidate_index = prepare_confirmation(workspace_root)
            self.assertEqual(len(candidate_index["candidates"]), 1)
            self.assertEqual(len(schedule["trials"]), 12)

            anytime = summarize_anytime(workspace_root)
            runs = {
                (item["algorithm"], run["seed"]): run
                for item in anytime["algorithms"]
                for run in item["runs"]
            }
            self.assertTrue(runs[("random", 17)]["hasQualifiedIncumbent"])
            self.assertFalse(runs[("random", 23)]["hasQualifiedIncumbent"])
            self.assertFalse(runs[("smac", 17)]["hasQualifiedIncumbent"])
            self.assertEqual(runs[("smac", 17)]["finalGeometricSpeedup"], 1.0)


if __name__ == "__main__":
    unittest.main()
