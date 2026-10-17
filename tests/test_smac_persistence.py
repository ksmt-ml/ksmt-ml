import tempfile
import unittest
from pathlib import Path

from ksmt_ml.smac_adapter import SmacAskTellStrategy
from ksmt_ml.smac_search import SmacNativeCheckpointStore
from ksmt_ml.search import TrialResult


def parameter_space():
    return {
        "schemaVersion": "cvc5-parameter-space-v0",
        "parameterSpaceId": "smac-persistence-test-v0",
        "targetSolver": "cvc5",
        "compatibleSolverVersions": ["1.3.4"],
        "applicationLifecycle": "fresh-solver",
        "backendRole": "regular-bv",
        "inventoryId": "test-v0",
        "options": {
            "option": {"kind": "boolean", "default": False},
        },
    }


def build_strategy(root: Path) -> SmacAskTellStrategy:
    return SmacAskTellStrategy.build(
        parameter_space(),
        instance_ids=["profile-v0"],
        seed=17,
        n_trials=3,
        repetitions=1,
        output_directory=root / "smac",
        additional_initial_configurations=0,
        overwrite=False,
        logging_level=False,
    )


class SmacPersistenceTest(unittest.TestCase):
    def test_running_trial_survives_native_corruption_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoints = SmacNativeCheckpointStore(root)
            strategy = build_strategy(root)

            empty = checkpoints.capture(strategy)
            self.assertEqual(
                empty["runhistoryStats"],
                {"submitted": 0, "finished": 0, "running": 0},
            )

            request = strategy.ask()
            running = checkpoints.capture(strategy)
            self.assertEqual(
                running["runhistoryStats"],
                {"submitted": 1, "finished": 0, "running": 1},
            )

            working = Path(strategy.facade.scenario.output_directory)
            (working / "runhistory.json").write_text("{broken", encoding="utf-8")
            checkpoints.restore(running, working)

            resumed = build_strategy(root)
            resumed.restore_proposal_index(1)
            resumed.attach_pending(request)
            resumed.validate_external_counts(completed_trials=0, has_pending_trial=True)
            resumed.tell(
                TrialResult(
                    request_id=request.request_id,
                    candidate_id=request.candidate["candidateId"],
                    instance_id=request.instance_id,
                    seed=request.seed,
                    repetition=request.repetition,
                    budget=request.budget,
                    status="SUCCESS",
                    cost=0.75,
                    duration_nanos=1_000_000,
                    censored=False,
                    correctness="CORRECT",
                    cache_hit=False,
                )
            )
            finished = checkpoints.capture(resumed)
            self.assertEqual(
                finished["runhistoryStats"],
                {"submitted": 1, "finished": 1, "running": 0},
            )
            self.assertEqual(resumed.incumbent_snapshot()["cost"], 0.75)

            checkpoints.restore(finished, working)
            restored = build_strategy(root)
            restored.restore_proposal_index(1)
            restored.validate_external_counts(completed_trials=1, has_pending_trial=False)
            self.assertEqual(restored.incumbent_snapshot()["cost"], 0.75)


if __name__ == "__main__":
    unittest.main()
