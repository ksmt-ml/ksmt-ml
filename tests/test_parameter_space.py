import json
import unittest
from pathlib import Path

from ksmt_ml.evaluator import EvaluatorError, normalize_candidate, validate_parameter_space
from ksmt_ml.parameter_space import singleton_smoke_candidates
from ksmt_ml.search import RandomSearchStrategy


ROOT = Path(__file__).resolve().parents[1]


class FrozenParameterSpaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = json.loads(
            (ROOT / "configs/cvc5-1.3.4-option-inventory-v0.json").read_text()
        )
        cls.space = json.loads(
            (ROOT / "configs/cvc5-regular-bv-parameter-space-v0.json").read_text()
        )

    def test_inventory_classifies_every_runtime_option(self):
        self.assertEqual(self.inventory["canonicalOptionCount"], 518)
        self.assertEqual(len(self.inventory["options"]), 518)
        self.assertEqual(sum(self.inventory["statusCounts"].values()), 518)
        self.assertEqual(
            self.inventory["statusCounts"]["active"], len(self.space["options"])
        )

    def test_parameter_space_is_valid_and_wide(self):
        validate_parameter_space(self.space)
        self.assertEqual(self.space["backendRole"], "regular-bv")
        self.assertGreaterEqual(len(self.space["options"]), 50)
        self.assertNotIn("tlimit-per", self.space["options"])
        self.assertNotIn("sat-random-seed", self.space["options"])

    def test_every_singleton_smoke_candidate_is_valid(self):
        candidates = singleton_smoke_candidates(self.space)
        self.assertGreater(len(candidates), len(self.space["options"]))
        for candidate in candidates:
            normalize_candidate(candidate, self.space)

    def test_random_search_never_emits_inactive_bv_sat_solver(self):
        strategy = RandomSearchStrategy(self.space, seed=19)
        for _ in range(100):
            candidate = strategy.ask().candidate
            self.assertNotIn("bitblast", candidate["options"])
            self.assertNotIn("bv-sat-solver", candidate["options"])


class TypedConstraintTest(unittest.TestCase):
    def setUp(self):
        self.space = {
            "schemaVersion": "cvc5-parameter-space-v0",
            "parameterSpaceId": "typed-test-v0",
            "inventoryId": "typed-test-inventory-v0",
            "targetSolver": "cvc5",
            "compatibleSolverVersions": ["1.3.4"],
            "applicationLifecycle": "fresh-solver",
            "backendRole": "regular-bv",
            "options": {
                "mode": {
                    "kind": "categorical",
                    "values": ["off", "on"],
                    "default": "off",
                },
                "child": {
                    "kind": "boolean",
                    "default": False,
                    "activeWhen": {"option": "mode", "equals": "on"},
                },
            },
            "constraints": [
                {
                    "kind": "forbid",
                    "when": {
                        "all": [
                            {"option": "mode", "equals": "on"},
                            {"option": "child", "equals": True},
                        ]
                    },
                    "reason": "test forbidden pair",
                }
            ],
        }

    def candidate(self, options):
        return {
            "schemaVersion": "cvc5-replay-candidate-v0",
            "candidateId": "candidate-v0",
            "parameterSpaceId": "typed-test-v0",
            "targetSolver": "cvc5",
            "options": options,
        }

    def test_rejects_setting_inactive_parameter(self):
        with self.assertRaisesRegex(EvaluatorError, "inactive"):
            normalize_candidate(self.candidate({"child": True}), self.space)

    def test_rejects_typed_forbidden_combination(self):
        with self.assertRaisesRegex(EvaluatorError, "test forbidden pair"):
            normalize_candidate(
                self.candidate({"mode": "on", "child": True}), self.space
            )

    def test_accepts_active_constraint_valid_parameter(self):
        normalize_candidate(
            self.candidate({"mode": "on", "child": False}), self.space
        )


if __name__ == "__main__":
    unittest.main()
