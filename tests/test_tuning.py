import unittest

from ksmt_ml.tuning import _build_parser, _translated_run_argv


def parse(strategy):
    return _build_parser().parse_args(
        [
            "run",
            "--strategy",
            strategy,
            "--run-dir",
            "/tmp/run",
            "--parameter-space",
            "/tmp/space.json",
            "--baseline",
            "/tmp/baseline.json",
            "--baseline-manifest",
            "/tmp/manifest.json",
            "--tsa-cli-jar",
            "/tmp/tsa.jar",
            "--profile-dir",
            "/tmp/profiles",
            "--max-wall-time-seconds",
            "60",
            "--per-trial-timeout-seconds",
            "10",
        ]
    )


class UnifiedTuningInterfaceTest(unittest.TestCase):
    def test_all_strategies_share_common_experiment_flags(self):
        translated = {}
        for strategy in ("random", "evolution", "smac"):
            backend, argv = _translated_run_argv(parse(strategy))
            translated[strategy] = (backend, argv)
            for flag in (
                "--run-dir",
                "--cache-dir",
                "--parameter-space",
                "--baseline",
                "--baseline-manifest",
                "--tsa-cli-jar",
                "--profile-dir",
                "--seed",
                "--max-wall-time-seconds",
                "--per-trial-timeout-seconds",
            ):
                self.assertIn(flag, argv)

        self.assertEqual(translated["random"][0], "search")
        self.assertEqual(translated["evolution"][0], "search")
        self.assertEqual(translated["smac"][0], "smac")
        self.assertIn("--max-evaluations", translated["random"][1])
        self.assertIn("--max-evaluations", translated["evolution"][1])
        self.assertIn("--max-trials", translated["smac"][1])


if __name__ == "__main__":
    unittest.main()
