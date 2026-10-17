"""Unified command-line interface for Random Search, evolution, and SMAC3."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from ksmt_ml import search, smac_search
from ksmt_ml.evaluator import load_json


def _translated_run_argv(arguments: argparse.Namespace) -> tuple[str, list[str]]:
    common = [
        "run",
        "--run-dir",
        str(arguments.run_dir),
        "--cache-dir",
        str(arguments.cache_dir),
        "--parameter-space",
        str(arguments.parameter_space),
        "--baseline",
        str(arguments.baseline),
        "--baseline-manifest",
        str(arguments.baseline_manifest),
        "--tsa-cli-jar",
        str(arguments.tsa_cli_jar),
        "--backend-role",
        arguments.backend_role,
        "--repetitions",
        str(arguments.repetitions),
        "--warmups",
        str(arguments.warmups),
        "--seed",
        str(arguments.seed),
        "--max-wall-time-seconds",
        str(arguments.max_wall_time_seconds),
        "--per-trial-timeout-seconds",
        str(arguments.per_trial_timeout_seconds),
        "--timeout-penalty",
        str(arguments.timeout_penalty),
        "--reward-clip",
        str(arguments.reward_clip),
        "--progress-interval-seconds",
        str(arguments.progress_interval_seconds),
        "--java",
        arguments.java,
    ]
    if arguments.profile_dir is not None:
        common.extend(("--profile-dir", str(arguments.profile_dir)))
    for profile in arguments.profile:
        common.extend(("--profile", str(profile)))
    if arguments.include_recorded_failures:
        common.append("--include-recorded-failures")
    if arguments.quiet_child_output:
        common.append("--quiet-child-output")

    if arguments.strategy == "smac":
        common.extend(
            (
                "--max-trials",
                str(arguments.max_optimizer_trials),
                "--additional-initial-configurations",
                str(arguments.smac_initial_configurations),
            )
        )
        return "smac", common

    common.extend(
        (
            "--strategy",
            arguments.strategy,
            "--max-evaluations",
            str(arguments.max_optimizer_trials),
            "--max-consecutive-duplicates",
            str(arguments.max_consecutive_duplicates),
            "--random-mutation-probability",
            str(arguments.random_mutation_probability),
            "--population-size",
            str(arguments.population_size),
            "--elite-fraction",
            str(arguments.elite_fraction),
            "--evolution-initial-mutation-probability",
            str(arguments.evolution_initial_mutation_probability),
            "--evolution-mutation-probability",
            str(arguments.evolution_mutation_probability),
        )
    )
    return "search", common


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--strategy", choices=("random", "evolution", "smac"), required=True)
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--cache-dir", type=Path, default=Path("experiments/cache-v0"))
    run.add_argument("--parameter-space", type=Path, required=True)
    run.add_argument("--baseline", type=Path, required=True)
    run.add_argument("--baseline-manifest", type=Path, required=True)
    run.add_argument("--tsa-cli-jar", type=Path, required=True)
    run.add_argument("--profile-dir", type=Path)
    run.add_argument("--profile", type=Path, action="append", default=[])
    run.add_argument("--backend-role", default="regular-bv")
    run.add_argument("--repetitions", type=int, default=1)
    run.add_argument("--warmups", type=int, default=0)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--max-optimizer-trials", type=int, default=1_000_000)
    run.add_argument("--max-wall-time-seconds", type=float, required=True)
    run.add_argument("--per-trial-timeout-seconds", type=float, required=True)
    run.add_argument("--timeout-penalty", type=float, default=10.0)
    run.add_argument("--reward-clip", type=float, default=5.0)
    run.add_argument("--include-recorded-failures", action="store_true")
    run.add_argument("--progress-interval-seconds", type=int, default=15)
    run.add_argument("--quiet-child-output", action="store_true")
    run.add_argument("--java", default="java")
    run.add_argument("--max-consecutive-duplicates", type=int, default=1000)
    run.add_argument("--random-mutation-probability", type=float, default=0.05)
    run.add_argument("--population-size", type=int, default=8)
    run.add_argument("--elite-fraction", type=float, default=0.25)
    run.add_argument("--evolution-initial-mutation-probability", type=float, default=0.05)
    run.add_argument("--evolution-mutation-probability", type=float, default=0.10)
    run.add_argument("--smac-initial-configurations", type=int, default=8)

    status = commands.add_parser("status")
    status.add_argument("--run-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.command == "run":
        backend, translated = _translated_run_argv(arguments)
        return smac_search.main(translated) if backend == "smac" else search.main(translated)

    manifest = load_json(arguments.run_dir / "manifest.json")
    status_argv = ["status", "--run-dir", str(arguments.run_dir)]
    if manifest.get("schemaVersion") == "cvc5-smac-search-manifest-v0":
        return smac_search.main(status_argv)
    return search.main(status_argv)


if __name__ == "__main__":
    raise SystemExit(main())
