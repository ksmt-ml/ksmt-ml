import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ksmt_ml.campaign import (
    CampaignError,
    budget_advice,
    extract_finalists,
    summarize_scores,
    validate_score_artifact,
)


def candidate(candidate_id):
    return {
        "schemaVersion": "cvc5-replay-candidate-v0",
        "candidateId": candidate_id,
        "parameterSpaceId": "space-v0",
        "targetSolver": "cvc5",
        "options": {},
    }


def score(candidate_id, rewards):
    profiles = [
        {
            "profileId": f"profile-{index}",
            "baselineCostNanos": 100.0,
            "candidateCostNanos": 100.0 / math.exp(reward),
            "reward": reward,
            "wrongAnswerCount": 0,
            "knownStatusTimeoutCount": 0,
            "failedReplayCount": 0,
        }
        for index, reward in enumerate(rewards)
    ]
    return {
        "schemaVersion": "cvc5-evaluator-score-v0",
        "baselineCandidateId": "default-v0",
        "candidateId": candidate_id,
        "parameterSpaceId": "space-v0",
        "backendRole": "regular-bv",
        "eligible": True,
        "reward": sum(rewards) / len(rewards),
        "policy": {
            "aggregation": "workload-macro-mean-log-speedup-v0",
            "objective": "non-check-plus-known-check-time-v0",
            "timeoutPenalty": 10.0,
            "rewardClip": 5.0,
            "excludeExpectedUnknownChecks": True,
            "excludeRecordedFailureProfiles": True,
        },
        "totals": {
            "comparedProfileCount": len(rewards),
            "excludedProfileCount": 0,
            "wrongAnswerCount": 0,
            "knownStatusTimeoutCount": 0,
            "failedReplayCount": 0,
        },
        "profiles": profiles,
        "excludedProfiles": [],
    }


class CampaignTest(unittest.TestCase):
    def test_resume_rejects_a_stale_score(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline.json"
            candidate_report = root / "candidate-report.json"
            score_path = root / "score.json"
            baseline.write_text("{}")
            candidate_report.write_text("{}")
            expected = score("candidate-v0", [0.1, 0.2])
            score_path.write_text(json.dumps(expected))

            with mock.patch(
                "ksmt_ml.campaign.score_reports", return_value=expected
            ):
                validate_score_artifact(
                    score_path=score_path,
                    baseline_path=baseline,
                    candidate_report_path=candidate_report,
                )

                stale = json.loads(json.dumps(expected))
                stale["policy"]["timeoutPenalty"] = 9.0
                score_path.write_text(json.dumps(stale))
                with self.assertRaisesRegex(CampaignError, "does not match"):
                    validate_score_artifact(
                        score_path=score_path,
                        baseline_path=baseline,
                        candidate_report_path=candidate_report,
                    )

    def test_budget_advice_requires_two_populations_and_timeout_margin(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline = Path(temporary) / "baseline.json"
            baseline.write_text(
                json.dumps(
                    {
                        "results": [
                            {"profileId": "profile-a", "wallDurationNanos": 2_000_000_000},
                            {"profileId": "profile-b", "wallDurationNanos": 3_000_000_000},
                        ]
                    }
                )
            )
            advice = budget_advice(
                baseline,
                search_budget_seconds=40,
                per_trial_timeout_seconds=8,
                population_size=4,
            )
            self.assertEqual(advice["estimatedEvolutionEvaluations"], 8)
            self.assertTrue(advice["ready"])

    def test_budget_advice_uses_per_profile_median_across_repetitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline = Path(temporary) / "baseline.json"
            baseline.write_text(
                json.dumps(
                    {
                        "results": [
                            {"profileId": "a", "wallDurationNanos": 1_000_000_000},
                            {"profileId": "a", "wallDurationNanos": 3_000_000_000},
                            {"profileId": "a", "wallDurationNanos": 2_000_000_000},
                            {"profileId": "b", "wallDurationNanos": 4_000_000_000},
                            {"profileId": "b", "wallDurationNanos": 6_000_000_000},
                            {"profileId": "b", "wallDurationNanos": 5_000_000_000},
                        ]
                    }
                )
            )

            advice = budget_advice(
                baseline,
                search_budget_seconds=60,
                per_trial_timeout_seconds=11,
                population_size=4,
                minimum_generations=2,
            )

            self.assertEqual(advice["defaultWholeWorkloadSeconds"], 7.0)
            self.assertEqual(advice["estimatedEvolutionEvaluations"], 8)
            self.assertTrue(advice["ready"])

    def test_extracts_both_incumbents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs = {}
            for name in ("random", "evolution"):
                run = root / name
                trial = run / "trials" / "trial-v0"
                trial.mkdir(parents=True)
                (trial / "candidate.json").write_text(
                    json.dumps(candidate(f"{name}-v0"))
                )
                (run / "state.json").write_text(
                    json.dumps(
                        {
                            "incumbent": {
                                "candidateId": f"{name}-v0",
                                "cost": 0.9,
                                "trialId": "trial-v0",
                            }
                        }
                    )
                )
                runs[name] = run
            smac = root / "smac"
            smac.mkdir()
            (smac / "state.json").write_text(
                json.dumps({"incumbent": {"candidate": candidate("smac-v0"), "cost": 0.8}})
            )

            paths = extract_finalists(
                runs["random"], runs["evolution"], smac, root / "finalists"
            )
            self.assertEqual(json.loads(paths["random"].read_text())["candidateId"], "random-v0")
            self.assertEqual(json.loads(paths["evolution"].read_text())["candidateId"], "evolution-v0")
            self.assertEqual(json.loads(paths["smac"].read_text())["candidateId"], "smac-v0")

    def test_summary_reports_paired_speedup_and_interval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            score_path = root / "score.json"
            score_path.write_text(json.dumps(score("candidate-v0", [0.1, 0.2, 0.3])))
            output = root / "summary.json"

            summary = summarize_scores(
                [score_path], output, minimum_profile_count=3
            )
            result = summary["results"][0]
            self.assertAlmostEqual(result["geometricSpeedup"], math.exp(0.2))
            self.assertGreater(result["bootstrap95MeanLogSpeedup"]["low"], 0)
            self.assertTrue(result["passesReplayGainGate"])
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
