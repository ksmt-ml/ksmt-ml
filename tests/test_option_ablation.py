import json
import tempfile
import unittest
from pathlib import Path

from ksmt_ml.option_ablation import (
    ABLATION_RESULTS_SCHEMA_VERSION,
    _select_entries,
    prepare_option_subset_ablation,
)


ROOT = Path(__file__).resolve().parents[1]


class OptionAblationTest(unittest.TestCase):
    def test_prepares_every_subset_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parameter_space_path = (
                ROOT / "configs" / "cvc5-regular-bv-parameter-space-v0.json"
            )
            parameter_space = json.loads(
                parameter_space_path.read_text(encoding="utf-8")
            )
            source_path = root / "source.json"
            source_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "cvc5-replay-candidate-v0",
                        "candidateId": "source-v0",
                        "parameterSpaceId": parameter_space["parameterSpaceId"],
                        "targetSolver": "cvc5",
                        "options": {
                            "ee-mode": "central",
                            "on-repeat-ite-simp": True,
                            "simp-with-care": True,
                            "theoryof-mode": "term",
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = root / "ablation"

            first = prepare_option_subset_ablation(
                source_candidate_path=source_path,
                parameter_space_path=parameter_space_path,
                output_directory=output,
            )
            second = prepare_option_subset_ablation(
                source_candidate_path=source_path,
                parameter_space_path=parameter_space_path,
                output_directory=output,
            )

            self.assertEqual(first, second)
            self.assertEqual(len(first["candidates"]), 16)
            self.assertEqual(
                {entry["mask"] for entry in first["candidates"]}, set(range(16))
            )
            self.assertEqual(first["candidates"][0]["optionNames"], [])
            self.assertEqual(
                set(first["candidates"][-1]["optionNames"]),
                set(first["policy"]["optionNames"]),
            )
            for entry in first["candidates"]:
                self.assertTrue((output / entry["path"]).is_file())

    def test_selects_only_candidates_that_passed_correctness(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries = [{"mask": mask} for mask in range(16)]
            results_path = root / "results-v0.json"
            rejected = {9, 11, 13, 15}
            results_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": ABLATION_RESULTS_SCHEMA_VERSION,
                        "sourceCandidateId": "source-v0",
                        "complete": True,
                        "results": [
                            {"mask": mask, "eligible": mask not in rejected}
                            for mask in range(16)
                        ],
                    }
                ),
                encoding="utf-8",
            )

            selected, selection = _select_entries(
                {
                    "sourceCandidate": {"candidateId": "source-v0"},
                    "candidates": entries,
                },
                eligibility_results_path=results_path,
            )

            self.assertEqual([entry["mask"] for entry in selected], [
                0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14
            ])
            self.assertEqual(selection["kind"], "eligible-from-results-v0")


if __name__ == "__main__":
    unittest.main()
