import importlib.util
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ksmt_ml.smac_adapter import (
    DEFAULT_SMAC_FAILURE_COST,
    SmacAdapterError,
    SmacAskTellStrategy,
    _normalize_instance_features,
    trial_result_to_smac_feedback,
)
from ksmt_ml.search import TrialResult


HAS_SMAC = (
    importlib.util.find_spec("ConfigSpace") is not None
    and importlib.util.find_spec("smac") is not None
)


def parameter_space():
    return {
        "schemaVersion": "cvc5-parameter-space-v0",
        "parameterSpaceId": "smac-adapter-test-v0",
        "targetSolver": "cvc5",
        "compatibleSolverVersions": ["1.3.4"],
        "applicationLifecycle": "fresh-solver",
        "backendRole": "regular-bv",
        "inventoryId": "test-v0",
        "options": {
            "option": {"kind": "boolean", "default": False},
        },
    }


def trial_result(
    *,
    request_id="trial-v0",
    candidate_id="candidate-v0",
    instance_id="profile-v0",
    seed=17,
    status="SUCCESS",
    cost=0.75,
    correctness="CORRECT",
    censored=False,
):
    return TrialResult(
        request_id=request_id,
        candidate_id=candidate_id,
        instance_id=instance_id,
        seed=seed,
        repetition=0,
        budget=None,
        status=status,
        cost=cost,
        duration_nanos=1_500_000_000,
        censored=censored,
        correctness=correctness,
        cache_hit=False,
    )


class FakeAdapter:
    def to_candidate(self, configuration):
        return {
            "schemaVersion": "cvc5-replay-candidate-v0",
            "candidateId": "candidate-v0",
            "parameterSpaceId": "space-v0",
            "targetSolver": "cvc5",
            "options": dict(configuration),
        }


class FakeTrialValue:
    def __init__(self, **values):
        self.values = values


class FakeStatusType:
    SUCCESS = "smac-success"
    TIMEOUT = "smac-timeout"
    CRASHED = "smac-crashed"


class FakeFacade:
    def __init__(self, info):
        self.info = info
        self.told = []

    def ask(self):
        return self.info

    def tell(self, info, value):
        self.told.append((info, value))


class SmacFeedbackTest(unittest.TestCase):
    def test_default_instance_features_distinguish_profiles(self):
        features = _normalize_instance_features(["a", "b", "c"], None)

        self.assertEqual(features["a"], [1.0, 0.0, 0.0])
        self.assertEqual(features["b"], [0.0, 1.0, 0.0])
        self.assertEqual(features["c"], [0.0, 0.0, 1.0])

    def test_explicit_instance_features_must_cover_every_profile(self):
        with self.assertRaisesRegex(SmacAdapterError, "exactly every"):
            _normalize_instance_features(["a", "b"], {"a": [1.0]})

    def test_success_preserves_measured_normalized_cost(self):
        feedback = trial_result_to_smac_feedback(trial_result())

        self.assertEqual(feedback.status, "SUCCESS")
        self.assertEqual(feedback.cost, 0.75)
        self.assertEqual(feedback.time_seconds, 1.5)
        self.assertEqual(feedback.additional_info["correctness"], "CORRECT")

    def test_known_timeout_is_censored_and_not_reported_as_crash(self):
        feedback = trial_result_to_smac_feedback(
            trial_result(
                status="KNOWN_STATUS_TIMEOUT",
                cost=None,
                correctness="NOT_ESTABLISHED",
                censored=True,
            )
        )

        self.assertEqual(feedback.status, "TIMEOUT")
        self.assertEqual(feedback.cost, DEFAULT_SMAC_FAILURE_COST)
        self.assertTrue(feedback.additional_info["censored"])

    def test_wrong_answer_can_never_become_successful_observation(self):
        feedback = trial_result_to_smac_feedback(
            trial_result(
                status="WRONG_ANSWER",
                cost=None,
                correctness="INCORRECT",
            )
        )

        self.assertEqual(feedback.status, "CRASHED")
        self.assertEqual(feedback.cost, DEFAULT_SMAC_FAILURE_COST)
        self.assertEqual(feedback.additional_info["correctness"], "INCORRECT")

    def test_failure_cost_must_be_finite(self):
        with self.assertRaisesRegex(SmacAdapterError, "finite and positive"):
            trial_result_to_smac_feedback(trial_result(), crash_cost=math.inf)


class SmacAskTellTest(unittest.TestCase):
    def strategy(self, *, budget=None):
        info = SimpleNamespace(
            config={"option": True},
            instance="profile-v0",
            seed=17,
            budget=budget,
        )
        facade = FakeFacade(info)
        return (
            SmacAskTellStrategy(
                adapter=FakeAdapter(),
                facade=facade,
                trial_value_type=FakeTrialValue,
                trial_info_type=SimpleNamespace,
                status_type=FakeStatusType,
                instance_ids=["profile-v0"],
                crash_cost=DEFAULT_SMAC_FAILURE_COST,
            ),
            facade,
        )

    def test_round_trip_keeps_instance_seed_and_typed_status(self):
        strategy, facade = self.strategy()
        request = strategy.ask()
        result = trial_result(
            request_id=request.request_id,
            candidate_id=request.candidate["candidateId"],
            instance_id=request.instance_id,
            seed=request.seed,
        )

        feedback = strategy.tell(result)

        self.assertEqual(request.instance_id, "profile-v0")
        self.assertEqual(request.seed, 17)
        self.assertIsNone(request.budget)
        self.assertEqual(feedback.status, "SUCCESS")
        self.assertEqual(facade.told[0][1].values["status"], "smac-success")
        self.assertEqual(strategy.pending_request_ids, ())

    def test_unqualified_fidelity_budget_is_rejected(self):
        strategy, _ = self.strategy(budget=0.5)

        with self.assertRaisesRegex(SmacAdapterError, "before any fidelity"):
            strategy.ask()

    def test_result_must_match_the_exact_pending_request(self):
        strategy, _ = self.strategy()
        request = strategy.ask()

        with self.assertRaisesRegex(SmacAdapterError, "field: instance_id"):
            strategy.tell(
                trial_result(
                    request_id=request.request_id,
                    candidate_id=request.candidate["candidateId"],
                    instance_id="another-profile-v0",
                    seed=request.seed,
                )
            )


@unittest.skipUnless(HAS_SMAC, "SMAC3 is an optional search dependency")
class SmacPinnedApiIntegrationTest(unittest.TestCase):
    def test_pinned_facade_completes_one_real_ask_tell_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            strategy = SmacAskTellStrategy.build(
                parameter_space(),
                instance_ids=["profile-v0"],
                seed=17,
                n_trials=2,
                repetitions=1,
                output_directory=Path(temporary),
                additional_initial_configurations=0,
            )
            request = strategy.ask()

            feedback = strategy.tell(
                trial_result(
                    request_id=request.request_id,
                    candidate_id=request.candidate["candidateId"],
                    instance_id=request.instance_id,
                    seed=request.seed,
                )
            )

            self.assertEqual(feedback.status, "SUCCESS")
            self.assertEqual(len(strategy.facade.runhistory), 1)


if __name__ == "__main__":
    unittest.main()
