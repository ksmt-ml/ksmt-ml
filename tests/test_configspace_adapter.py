import importlib.util
import unittest
from pathlib import Path

from ksmt_ml.configspace_adapter import ZERO_SELECTOR_SUFFIX, build_configspace
from ksmt_ml.evaluator import load_json


HAS_CONFIGSPACE = importlib.util.find_spec("ConfigSpace") is not None


def parameter_space():
    return {
        "schemaVersion": "cvc5-parameter-space-v0",
        "parameterSpaceId": "configspace-test-v0",
        "targetSolver": "cvc5",
        "compatibleSolverVersions": ["1.3.4"],
        "applicationLifecycle": "fresh-solver",
        "backendRole": "regular-bv",
        "inventoryId": "test-v0",
        "options": {
            "enabled": {"kind": "boolean", "default": False},
            "mode": {
                "kind": "categorical",
                "values": ["off", "on"],
                "default": "off",
            },
            "child": {
                "kind": "integer",
                "minimum": 1,
                "maximum": 5,
                "step": 1,
                "default": 2,
                "activeWhen": {"option": "mode", "equals": "on"},
            },
            "ratio": {
                "kind": "number",
                "minimum": 0.0,
                "maximum": 100.0,
                "positiveMinimum": 0.01,
                "sampling": "log-uniform-or-zero",
                "default": 0.0,
            },
        },
        "constraints": [
            {
                "kind": "forbid",
                "when": {
                    "all": [
                        {"option": "enabled", "equals": True},
                        {"option": "mode", "equals": "off"},
                    ]
                },
                "reason": "test forbidden combination",
            }
        ],
    }


@unittest.skipUnless(HAS_CONFIGSPACE, "ConfigSpace is an optional search dependency")
class ConfigSpaceAdapterTest(unittest.TestCase):
    def test_default_round_trip_does_not_emit_synthetic_options(self):
        adapter = build_configspace(parameter_space(), seed=7)

        candidate = adapter.to_candidate(adapter.configspace.get_default_configuration())

        self.assertEqual(candidate["options"], {})
        self.assertNotIn(f"ratio{ZERO_SELECTOR_SUFFIX}", candidate["options"])
        restored = adapter.from_candidate(candidate)
        self.assertEqual(dict(restored), dict(adapter.configspace.get_default_configuration()))

    def test_zero_union_and_active_condition_round_trip(self):
        adapter = build_configspace(parameter_space(), seed=7)
        candidate = {
            "schemaVersion": "cvc5-replay-candidate-v0",
            "candidateId": "candidate-v0",
            "parameterSpaceId": "configspace-test-v0",
            "targetSolver": "cvc5",
            "options": {"mode": "on", "child": 5},
        }

        configuration = adapter.from_candidate(candidate)
        restored = adapter.to_candidate(configuration, candidate_id="restored-v0")

        self.assertEqual(restored["options"], candidate["options"])
        self.assertTrue(configuration[f"ratio{ZERO_SELECTOR_SUFFIX}"])

    def test_sampled_configurations_always_decode_to_valid_candidates(self):
        adapter = build_configspace(parameter_space(), seed=11)

        for configuration in adapter.configspace.sample_configuration(20):
            candidate = adapter.to_candidate(configuration)
            adapter.from_candidate(candidate).check_valid_configuration()

    def test_checked_in_cvc5_space_builds_and_round_trips_samples(self):
        root = Path(__file__).resolve().parents[1]
        source = load_json(root / "configs/cvc5-regular-bv-parameter-space-v0.json")
        adapter = build_configspace(source, seed=19)

        for configuration in adapter.configspace.sample_configuration(10):
            adapter.from_candidate(adapter.to_candidate(configuration))


if __name__ == "__main__":
    unittest.main()
