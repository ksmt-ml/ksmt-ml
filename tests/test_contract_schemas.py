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
        self.assertIn("parameterSpaceId", schema["required"])
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
        self.assertEqual(schema["properties"]["targetSolver"]["const"], "cvc5")
        self.assertIn("parameterSpaceId", schema["required"])
        self.assertEqual(
            schema["properties"]["applicationLifecycle"]["const"],
            "fresh-solver",
        )
        self.assertIn("backendRole", schema["required"])
        self.assertIn("inventoryId", schema["required"])
        self.assertEqual(
            schema["$defs"]["constraint"]["properties"]["kind"]["const"],
            "forbid",
        )


if __name__ == "__main__":
    unittest.main()
