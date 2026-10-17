import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContractSchemaTest(unittest.TestCase):
    def load_schema(self, name: str) -> dict:
        with (ROOT / "schemas" / name).open(encoding="utf-8") as source:
            return json.load(source)

    def test_bundle_schema_targets_pretrained_cvc5_model(self) -> None:
        schema = self.load_schema("cvc5-tuner-bundle-v0.schema.json")

        self.assertEqual(schema["properties"]["targetSolver"]["const"], "cvc5")
        self.assertEqual(
            schema["properties"]["applicationLifecycle"]["const"],
            "fresh-solver",
        )

    def test_parameter_space_is_versioned(self) -> None:
        schema = self.load_schema("cvc5-parameter-space-v0.schema.json")

        self.assertEqual(
            schema["properties"]["schemaVersion"]["const"],
            "cvc5-parameter-space-v0",
        )

    def test_smac_persistence_contracts_remain_v0(self) -> None:
        expected = {
            "cvc5-smac-search-manifest-v0.schema.json": "cvc5-smac-search-manifest-v0",
            "cvc5-smac-search-state-v0.schema.json": "cvc5-smac-search-state-v0",
            "cvc5-smac-search-event-v0.schema.json": "cvc5-smac-search-event-v0",
            "cvc5-smac-native-checkpoint-v0.schema.json": "cvc5-smac-native-checkpoint-v0",
            "cvc5-tuning-campaign-summary-v0.schema.json": "cvc5-tuning-campaign-summary-v0",
        }

        for name, version in expected.items():
            with self.subTest(name=name):
                schema = self.load_schema(name)
                self.assertEqual(schema["properties"]["schemaVersion"]["const"], version)


if __name__ == "__main__":
    unittest.main()
