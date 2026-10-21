"""Small file-only helpers for a development/holdout tuning campaign."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any, Sequence

from ksmt_ml.evaluator import (
    DEFAULT_TIMEOUT_PENALTY,
    describe_workload,
    load_json,
    normalize_candidate,
    score_reports,
    validate_baseline_manifest,
    validate_report_against_workload,
    validate_score,
    write_json,
)


class CampaignError(ValueError):
    pass


def validate_replay_artifact(
    *,
    report_path: Path,
    candidate_path: Path,
    parameter_space_path: Path,
    profile_directory: Path | None = None,
    profiles: Sequence[Path] = (),
    backend_role: str = "regular-bv",
    repetitions: int,
    warmups: int,
) -> None:
    """Validate a completed report before a resumed campaign skips it."""

    parameter_space = load_json(parameter_space_path)
    workload = describe_workload(
        profile_directory=profile_directory,
        profiles=profiles,
        backend_role=backend_role,
    )
    expected_candidate = normalize_candidate(
        load_json(candidate_path), parameter_space
    )
    report = load_json(report_path)
    validate_report_against_workload(
        report,
        parameter_space,
        workload,
        expected_repetitions=repetitions,
        expected_warmups=warmups,
    )
    if report["candidate"] != expected_candidate:
        raise CampaignError("replay report belongs to another candidate artifact")


def validate_baseline_artifacts(
    *,
    report_path: Path,
    manifest_path: Path,
    parameter_space_path: Path,
    tsa_cli_jar: Path,
    profile_directory: Path | None = None,
    profiles: Sequence[Path] = (),
    backend_role: str = "regular-bv",
    timeout_penalty: float = DEFAULT_TIMEOUT_PENALTY,
) -> None:
    """Validate a sealed baseline and every content binding needed for reuse."""

    parameter_space = load_json(parameter_space_path)
    report = load_json(report_path)
    workload = describe_workload(
        profile_directory=profile_directory,
        profiles=profiles,
        backend_role=backend_role,
    )
    validate_baseline_manifest(
        load_json(manifest_path),
        report_path=report_path,
        report=report,
        parameter_space_path=parameter_space_path,
        parameter_space=parameter_space,
        tsa_cli_jar=tsa_cli_jar,
        current_workload=workload,
        expected_timeout_penalty=timeout_penalty,
    )


def validate_score_artifact(
    *,
    score_path: Path,
    baseline_path: Path,
    candidate_report_path: Path,
    timeout_penalty: float = DEFAULT_TIMEOUT_PENALTY,
    reward_clip: float = 5.0,
    exclude_recorded_failures: bool = True,
) -> None:
    """Reject a stale score instead of trusting only its candidate identifier."""

    actual = load_json(score_path)
    expected = score_reports(
        load_json(baseline_path),
        load_json(candidate_report_path),
        timeout_penalty=timeout_penalty,
        reward_clip=reward_clip,
        exclude_recorded_failures=exclude_recorded_failures,
    )
    validate_score(actual, expected_candidate_id=expected["candidateId"])
    if actual != expected:
        raise CampaignError("persisted score does not match the current reports")


def budget_advice(
    baseline_path: Path,
    *,
    search_budget_seconds: float,
    per_trial_timeout_seconds: float,
    population_size: int,
    minimum_generations: int = 2,
) -> dict[str, Any]:
    if search_budget_seconds <= 0 or per_trial_timeout_seconds <= 0:
        raise CampaignError("search and per-trial budgets must be positive")
    if population_size < 2 or minimum_generations < 1:
        raise CampaignError("population must be at least 2 and generations positive")
    baseline = load_json(baseline_path)
    results = baseline.get("results")
    if not isinstance(results, list) or not results:
        raise CampaignError("baseline has no replay results")
    durations_by_profile: dict[str, list[float]] = {}
    for item in results:
        profile_id = item.get("profileId")
        duration = item.get("wallDurationNanos")
        if not isinstance(profile_id, str) or not profile_id:
            raise CampaignError("baseline result has no profile id")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise CampaignError("baseline result has invalid wall duration")
        durations_by_profile.setdefault(profile_id, []).append(float(duration))
    default_seconds = sum(
        statistics.median(durations) for durations in durations_by_profile.values()
    ) / 1e9
    if not math.isfinite(default_seconds) or default_seconds <= 0:
        raise CampaignError("baseline has invalid total wall duration")
    required_evaluations = population_size * minimum_generations
    estimated_evaluations = math.floor(search_budget_seconds / default_seconds)
    timeout_ok = per_trial_timeout_seconds >= default_seconds * 1.5
    budget_ok = estimated_evaluations >= required_evaluations
    return {
        "defaultWholeWorkloadSeconds": default_seconds,
        "estimatedEvolutionEvaluations": estimated_evaluations,
        "requiredEvolutionEvaluations": required_evaluations,
        "perTrialTimeoutHas50PercentMargin": timeout_ok,
        "twoGenerationBudget": budget_ok,
        "ready": timeout_ok and budget_ok,
    }


def extract_finalists(
    random_run: Path,
    evolution_run: Path,
    smac_run: Path,
    output_directory: Path,
) -> dict[str, Path]:
    ordinary_candidates: dict[str, dict[str, Any]] = {}
    for name, run in (("random", random_run), ("evolution", evolution_run)):
        state = load_json(run / "state.json")
        incumbent = state.get("incumbent")
        if not isinstance(incumbent, dict):
            raise CampaignError(f"{name} run has no eligible incumbent")
        candidate_path = run / "trials" / incumbent["trialId"] / "candidate.json"
        if not candidate_path.is_file():
            raise CampaignError(f"{name} incumbent candidate artifact is missing")
        ordinary_candidates[name] = load_json(candidate_path)

    smac_state = load_json(smac_run / "state.json")
    smac_incumbent = smac_state.get("incumbent")
    if not isinstance(smac_incumbent, dict) or not isinstance(
        smac_incumbent.get("candidate"), dict
    ):
        raise CampaignError("SMAC run has no eligible incumbent")

    output_directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "random": output_directory / "random-candidate-v0.json",
        "evolution": output_directory / "evolution-candidate-v0.json",
        "smac": output_directory / "smac-candidate-v0.json",
    }
    write_json(paths["random"], ordinary_candidates["random"])
    write_json(paths["evolution"], ordinary_candidates["evolution"])
    write_json(paths["smac"], smac_incumbent["candidate"])
    return paths


def summarize_scores(
    score_paths: Sequence[Path],
    output: Path,
    *,
    minimum_relative_improvement: float = 0.05,
    minimum_profile_count: int = 5,
    minimum_positive_profile_fraction: float = 0.60,
) -> dict[str, Any]:
    if not 0 < minimum_relative_improvement < 1:
        raise CampaignError("minimum relative improvement must be in (0, 1)")
    if minimum_profile_count < 1:
        raise CampaignError("minimum profile count must be positive")
    if not 0 < minimum_positive_profile_fraction <= 1:
        raise CampaignError("minimum positive profile fraction must be in (0, 1]")
    entries = []
    for path in score_paths:
        score = load_json(path)
        validate_score(score)
        rewards = [float(item["reward"]) for item in score["profiles"]]
        low, high = _bootstrap_mean_interval(rewards, seed=20261016)
        reward = float(score["reward"])
        relative_improvement = 1.0 - math.exp(-reward)
        positive_count = sum(value > 0 for value in rewards)
        failure_free = (
            score["totals"]["wrongAnswerCount"] == 0
            and score["totals"]["knownStatusTimeoutCount"] == 0
            and score["totals"]["failedReplayCount"] == 0
        )
        gate_checks = {
            "eligible": score["eligible"],
            "minimumImprovement": relative_improvement
            >= minimum_relative_improvement,
            "positiveInterval": low > 0,
            "minimumProfileCount": len(rewards) >= minimum_profile_count,
            "positiveProfileFraction": positive_count / len(rewards)
            >= minimum_positive_profile_fraction,
            "failureFree": failure_free,
        }
        entries.append(
            {
                "score": str(path.resolve()),
                "candidateId": score["candidateId"],
                "eligible": score["eligible"],
                "profileCount": len(rewards),
                "meanLogSpeedup": reward,
                "geometricSpeedup": math.exp(reward),
                "relativeImprovement": relative_improvement,
                "bootstrap95MeanLogSpeedup": {"low": low, "high": high},
                "positiveProfileCount": positive_count,
                "negativeProfileCount": sum(value < 0 for value in rewards),
                "wrongAnswerCount": score["totals"]["wrongAnswerCount"],
                "knownStatusTimeoutCount": score["totals"][
                    "knownStatusTimeoutCount"
                ],
                "failedReplayCount": score["totals"]["failedReplayCount"],
                "gateChecks": gate_checks,
                "passesReplayGainGate": all(gate_checks.values()),
            }
        )
    summary = {
        "schemaVersion": "cvc5-tuning-campaign-summary-v0",
        "gate": {
            "minimumRelativeImprovement": minimum_relative_improvement,
            "minimumProfileCount": minimum_profile_count,
            "minimumPositiveProfileFraction": minimum_positive_profile_fraction,
            "requirePositiveBootstrap95LowerBound": True,
            "requireEligibleFailureFreeReplay": True,
        },
        "interpretation": (
            "A replay-level gain is confirmed only for an eligible candidate whose "
            "predeclared minimum improvement is met and whose paired holdout interval "
            "stays above zero. End-to-end TSA gain requires a separate fresh-run gate."
        ),
        "results": entries,
    }
    write_json(output, summary)
    return summary


def _bootstrap_mean_interval(
    values: Sequence[float], *, seed: int, samples: int = 10_000
) -> tuple[float, float]:
    if not values:
        raise CampaignError("cannot summarize an empty score")
    if len(values) == 1:
        return float(values[0]), float(values[0])
    generator = random.Random(seed)
    estimates = sorted(
        sum(generator.choice(values) for _ in values) / len(values)
        for _ in range(samples)
    )
    return estimates[int(0.025 * samples)], estimates[int(0.975 * samples) - 1]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("extract-finalists")
    extract.add_argument("--random-run", type=Path, required=True)
    extract.add_argument("--evolution-run", type=Path, required=True)
    extract.add_argument("--smac-run", type=Path, required=True)
    extract.add_argument("--output-dir", type=Path, required=True)
    summarize = commands.add_parser("summarize")
    summarize.add_argument("--score", type=Path, action="append", required=True)
    summarize.add_argument("--output", type=Path, required=True)
    summarize.add_argument("--minimum-relative-improvement", type=float, default=0.05)
    summarize.add_argument("--minimum-profile-count", type=int, default=5)
    summarize.add_argument(
        "--minimum-positive-profile-fraction", type=float, default=0.60
    )
    advice = commands.add_parser("budget-advice")
    advice.add_argument("--baseline", type=Path, required=True)
    advice.add_argument("--search-budget-seconds", type=float, required=True)
    advice.add_argument("--per-trial-timeout-seconds", type=float, required=True)
    advice.add_argument("--population-size", type=int, required=True)
    advice.add_argument("--minimum-generations", type=int, default=2)

    validate_replay = commands.add_parser(
        "validate-replay", help="validate and reuse an existing replay report"
    )
    validate_replay.add_argument("--report", type=Path, required=True)
    validate_replay.add_argument("--candidate", type=Path, required=True)
    validate_replay.add_argument("--parameter-space", type=Path, required=True)
    validate_replay.add_argument("--profile-dir", type=Path)
    validate_replay.add_argument("--profile", type=Path, action="append", default=[])
    validate_replay.add_argument("--backend-role", default="regular-bv")
    validate_replay.add_argument("--repetitions", type=int, required=True)
    validate_replay.add_argument("--warmups", type=int, required=True)

    validate_baseline = commands.add_parser(
        "validate-baseline", help="validate and reuse a sealed baseline"
    )
    validate_baseline.add_argument("--report", type=Path, required=True)
    validate_baseline.add_argument("--manifest", type=Path, required=True)
    validate_baseline.add_argument("--parameter-space", type=Path, required=True)
    validate_baseline.add_argument("--tsa-cli-jar", type=Path, required=True)
    validate_baseline.add_argument("--profile-dir", type=Path)
    validate_baseline.add_argument("--profile", type=Path, action="append", default=[])
    validate_baseline.add_argument("--backend-role", default="regular-bv")
    validate_baseline.add_argument(
        "--timeout-penalty", type=float, default=DEFAULT_TIMEOUT_PENALTY
    )

    validate_saved_score = commands.add_parser(
        "validate-score", help="validate a score against its current reports"
    )
    validate_saved_score.add_argument("--score", type=Path, required=True)
    validate_saved_score.add_argument("--baseline", type=Path, required=True)
    validate_saved_score.add_argument(
        "--candidate-report", type=Path, required=True
    )
    validate_saved_score.add_argument(
        "--timeout-penalty", type=float, default=DEFAULT_TIMEOUT_PENALTY
    )
    validate_saved_score.add_argument("--reward-clip", type=float, default=5.0)
    validate_saved_score.add_argument(
        "--include-recorded-failures", action="store_true"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        if arguments.command == "extract-finalists":
            paths = extract_finalists(
                arguments.random_run,
                arguments.evolution_run,
                arguments.smac_run,
                arguments.output_dir,
            )
            for name, path in paths.items():
                print(f"{name}: {path}")
        elif arguments.command == "summarize":
            summary = summarize_scores(
                arguments.score,
                arguments.output,
                minimum_relative_improvement=arguments.minimum_relative_improvement,
                minimum_profile_count=arguments.minimum_profile_count,
                minimum_positive_profile_fraction=arguments.minimum_positive_profile_fraction,
            )
            for item in summary["results"]:
                interval = item["bootstrap95MeanLogSpeedup"]
                print(
                    f"{item['candidateId']}: eligible={item['eligible']} "
                    f"speedup={item['geometricSpeedup']:.4f}x "
                    f"log95=[{interval['low']:.4f}, {interval['high']:.4f}] "
                    f"passesReplayGainGate={item['passesReplayGainGate']}"
                )
        elif arguments.command == "budget-advice":
            advice = budget_advice(
                arguments.baseline,
                search_budget_seconds=arguments.search_budget_seconds,
                per_trial_timeout_seconds=arguments.per_trial_timeout_seconds,
                population_size=arguments.population_size,
                minimum_generations=arguments.minimum_generations,
            )
            print(json.dumps(advice, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if advice["ready"] else 3
        elif arguments.command == "validate-replay":
            validate_replay_artifact(
                report_path=arguments.report,
                candidate_path=arguments.candidate,
                parameter_space_path=arguments.parameter_space,
                profile_directory=arguments.profile_dir,
                profiles=arguments.profile,
                backend_role=arguments.backend_role,
                repetitions=arguments.repetitions,
                warmups=arguments.warmups,
            )
        elif arguments.command == "validate-baseline":
            validate_baseline_artifacts(
                report_path=arguments.report,
                manifest_path=arguments.manifest,
                parameter_space_path=arguments.parameter_space,
                tsa_cli_jar=arguments.tsa_cli_jar,
                profile_directory=arguments.profile_dir,
                profiles=arguments.profile,
                backend_role=arguments.backend_role,
                timeout_penalty=arguments.timeout_penalty,
            )
        else:
            validate_score_artifact(
                score_path=arguments.score,
                baseline_path=arguments.baseline,
                candidate_report_path=arguments.candidate_report,
                timeout_penalty=arguments.timeout_penalty,
                reward_clip=arguments.reward_clip,
                exclude_recorded_failures=not arguments.include_recorded_failures,
            )
        return 0
    except (OSError, ValueError) as failure:
        print(f"campaign helper failed: {failure}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
