"""Budgeted, resumable search over real cvc5 profile replay evaluations.

The orchestration is deliberately file based.  A run consists of an immutable
manifest, an append-only JSONL event stream, an atomic checkpoint, and ordinary
JSON trial artifacts.  The evaluator is the real TSA/cvc5 replay adapter from
``ksmt_ml.evaluator``; this module contains no timing model or fake objective.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import signal
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from ksmt_ml.evaluator import (
    CANDIDATE_SCHEMA_VERSION,
    EvaluatorError,
    ExperimentInterrupted,
    InvalidReplayReportError,
    PROTECTED_CVC5_OPTIONS,
    ReplayProcessError,
    ReplayTimeoutError,
    active_option_names,
    aggregate_profile_trial_reports,
    canonical_json,
    describe_workload,
    load_json,
    normalize_candidate,
    run_replay,
    score_reports,
    select_profile_report,
    sha256_file,
    sha256_json,
    single_profile_workload,
    validate_baseline_manifest,
    validate_parameter_space,
    validate_report,
    validate_report_against_workload,
    validate_score,
    validate_workload_manifest,
)


SEARCH_MANIFEST_SCHEMA_VERSION = "cvc5-search-manifest-v0"
SEARCH_STATE_SCHEMA_VERSION = "cvc5-search-state-v0"
SEARCH_EVENT_SCHEMA_VERSION = "cvc5-search-event-v0"
SEARCH_CACHE_ENTRY_SCHEMA_VERSION = "cvc5-search-cache-entry-v0"
SEARCH_STRATEGY_STATE_SCHEMA_VERSION = "cvc5-random-search-state-v0"
EVOLUTION_STRATEGY_STATE_SCHEMA_VERSION = "cvc5-evolution-search-state-v0"
SEARCH_EVALUATOR_VERSION = "cvc5-real-per-profile-replay-search-v0"
RANDOM_SAMPLING_POLICY = "independent-default-mutation-v0"
EVOLUTION_SAMPLING_POLICY = "domain-aware-elitist-mutation-v0"
DEFAULT_RANDOM_MUTATION_PROBABILITY = 0.05
DEFAULT_EVOLUTION_INITIAL_MUTATION_PROBABILITY = 0.05
DEFAULT_EVOLUTION_MUTATION_PROBABILITY = 0.10
DEFAULT_EVOLUTION_POPULATION_SIZE = 8
DEFAULT_EVOLUTION_ELITE_FRACTION = 0.25
PROCESS_HEARTBEAT_INTERVAL_SECONDS = 5.0
SEARCH_EVENT_TYPES = frozenset(
    {
        "EXPERIMENT_CREATED",
        "EXPERIMENT_RESUMED",
        "DUPLICATE_SKIPPED",
        "CANDIDATE_PROPOSED",
        "TRIAL_RECOVERED",
        "CACHE_HIT",
        "TRIAL_RECOVERY_FAILED",
        "TRIAL_STARTED",
        "TRIAL_FINISHED",
        "INCUMBENT_CHANGED",
        "EXPERIMENT_INTERRUPTED",
        "UNCLEAN_TRIAL_RECOVERED",
        "EXPERIMENT_FINISHED",
    }
)


class SearchError(ValueError):
    """The search definition or persisted state is inconsistent."""


@dataclass(frozen=True)
class TrialRequest:
    """One optimizer-visible evaluation request.

    ``instance_id``, ``seed`` and ``budget`` deliberately mirror the concepts
    used by algorithm configurators such as SMAC.  Random Search uses the same
    contract so changing strategy cannot silently change evaluator semantics.
    """

    request_id: str
    proposal_index: int
    candidate: dict[str, Any]
    instance_id: str
    seed: int
    repetition: int
    budget: float | None


@dataclass(frozen=True)
class TrialResult:
    request_id: str
    candidate_id: str
    instance_id: str
    seed: int
    repetition: int
    budget: float | None
    status: str
    cost: float | None
    duration_nanos: int
    censored: bool
    correctness: str
    cache_hit: bool
    failure_message: str | None = None

    @property
    def eligible(self) -> bool:
        return (
            self.status == "SUCCESS"
            and self.correctness == "CORRECT"
            and self.cost is not None
        )


@dataclass(frozen=True)
class ProfileTrialEvaluation:
    """Raw and scored artifacts for one profile/repetition coordinate."""

    request: TrialRequest
    report: dict[str, Any]
    score: dict[str, Any]
    result: TrialResult


class CandidateDisqualifiedError(EvaluatorError):
    """A profile result that makes the complete candidate irreversibly invalid."""

    def __init__(
        self,
        request: TrialRequest,
        profile_result: TrialResult,
        duration_nanos: int,
    ):
        message = (
            f"candidate became ineligible on profile {profile_result.instance_id}: "
            f"{profile_result.status}"
        )
        super().__init__(message)
        self.result = TrialResult(
            request_id=request.request_id,
            candidate_id=request.candidate["candidateId"],
            instance_id=request.instance_id,
            seed=request.seed,
            repetition=request.repetition,
            budget=request.budget,
            status=profile_result.status,
            cost=None,
            duration_nanos=duration_nanos,
            censored=profile_result.censored,
            correctness=profile_result.correctness,
            cache_hit=False,
            failure_message=message,
        )


class SearchStrategy(Protocol):
    """Minimal strategy-neutral ask/tell contract."""

    def ask(self) -> TrialRequest: ...

    def tell(self, result: TrialResult) -> None: ...

    def reject(self, request: TrialRequest) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...

    def restore(self, state: Mapping[str, Any]) -> None: ...


class RandomSearchStrategy:
    """Deterministic independent sampling from every active option domain."""

    def __init__(
        self,
        parameter_space: Mapping[str, Any],
        seed: int,
        *,
        mutation_probability: float = DEFAULT_RANDOM_MUTATION_PROBABILITY,
        instance_id: str = "whole-workload-v0",
        evaluation_seed: int = 0,
        repetition: int = 0,
        budget: float | None = None,
    ):
        _validate_search_parameter_space(parameter_space)
        _validate_trial_coordinates(instance_id, evaluation_seed, repetition, budget)
        _validate_mutation_probability(mutation_probability)
        self._parameter_space = dict(parameter_space)
        self._parameter_space_hash = sha256_json(parameter_space)
        self._seed = seed
        self._mutation_probability = float(mutation_probability)
        self._instance_id = instance_id
        self._evaluation_seed = evaluation_seed
        self._repetition = repetition
        self._budget = budget
        self._next_proposal_index = 0
        self._observation_count = 0

    def ask(self) -> TrialRequest:
        proposal_index = self._next_proposal_index
        self._next_proposal_index += 1
        random_seed = hashlib.sha256(
            canonical_json(
                {
                    "strategy": "random-search-v0",
                    "seed": self._seed,
                    "parameterSpaceSha256": self._parameter_space_hash,
                    "proposalIndex": proposal_index,
                }
            )
        ).digest()
        generator = random.Random(int.from_bytes(random_seed, "big"))
        for _ in range(1_000):
            proposed = {
                name: (
                    _sample_non_default_domain(generator, name, domain)
                    if generator.random() < self._mutation_probability
                    else domain["default"]
                )
                for name, domain in sorted(self._parameter_space["options"].items())
            }
            active = active_option_names(self._parameter_space, proposed)
            options = {
                name: value
                for name, value in proposed.items()
                if name in active
                and value != self._parameter_space["options"][name]["default"]
            }
            trial_identity = {
                "parameterSpaceId": self._parameter_space["parameterSpaceId"],
                "targetSolver": "cvc5",
                "options": options,
            }
            trial_candidate = {
                "schemaVersion": CANDIDATE_SCHEMA_VERSION,
                "candidateId": f"validation-{proposal_index}",
                **trial_identity,
            }
            try:
                normalize_candidate(trial_candidate, self._parameter_space)
                break
            except EvaluatorError:
                continue
        else:
            raise SearchError("failed to sample a constraint-valid candidate")
        identity = {
            "parameterSpaceId": self._parameter_space["parameterSpaceId"],
            "targetSolver": "cvc5",
            "options": options,
        }
        candidate_id = f"cvc5-random-v0-{sha256_json(identity)[:20]}"
        candidate = {
            "schemaVersion": CANDIDATE_SCHEMA_VERSION,
            "candidateId": candidate_id,
            **identity,
        }
        normalize_candidate(candidate, self._parameter_space)
        request_id = _trial_request_id(
            candidate_id,
            self._instance_id,
            self._evaluation_seed,
            self._repetition,
            self._budget,
        )
        return TrialRequest(
            request_id=request_id,
            proposal_index=proposal_index,
            candidate=candidate,
            instance_id=self._instance_id,
            seed=self._evaluation_seed,
            repetition=self._repetition,
            budget=self._budget,
        )

    def tell(self, result: TrialResult) -> None:
        _validate_trial_result(result)
        self._observation_count += 1

    def reject(self, request: TrialRequest) -> None:
        _validate_trial_request(request)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": SEARCH_STRATEGY_STATE_SCHEMA_VERSION,
            "strategy": "random-search-v0",
            "seed": self._seed,
            "samplingPolicy": RANDOM_SAMPLING_POLICY,
            "mutationProbability": self._mutation_probability,
            "parameterSpaceSha256": self._parameter_space_hash,
            "instanceId": self._instance_id,
            "evaluationSeed": self._evaluation_seed,
            "repetition": self._repetition,
            "budget": self._budget,
            "nextProposalIndex": self._next_proposal_index,
            "observationCount": self._observation_count,
        }

    def restore(self, state: Mapping[str, Any]) -> None:
        expected = {
            "schemaVersion": SEARCH_STRATEGY_STATE_SCHEMA_VERSION,
            "strategy": "random-search-v0",
            "seed": self._seed,
            "samplingPolicy": RANDOM_SAMPLING_POLICY,
            "mutationProbability": self._mutation_probability,
            "parameterSpaceSha256": self._parameter_space_hash,
            "instanceId": self._instance_id,
            "evaluationSeed": self._evaluation_seed,
            "repetition": self._repetition,
            "budget": self._budget,
        }
        for key, value in expected.items():
            if state.get(key) != value:
                raise SearchError(f"incompatible strategy checkpoint field: {key}")
        next_index = state.get("nextProposalIndex")
        observations = state.get("observationCount")
        if not isinstance(next_index, int) or next_index < 0:
            raise SearchError("invalid nextProposalIndex in strategy checkpoint")
        if not isinstance(observations, int) or observations < 0:
            raise SearchError("invalid observationCount in strategy checkpoint")
        self._next_proposal_index = next_index
        self._observation_count = observations


class EvolutionSearchStrategy:
    """Serial `(mu, lambda)` evolutionary baseline for the mixed cvc5 space.

    Selection is performed only after a complete population.  Children inherit
    typed values from an elite parent and mutate each option in its native
    domain; categorical options are never mapped to an artificial numeric
    ordering.  Numeric values are resampled from their declared uniform/log
    domain when mutated.  The complete pending population is checkpointed so a
    process restart cannot change selection or the candidate sequence.
    """

    def __init__(
        self,
        parameter_space: Mapping[str, Any],
        seed: int,
        *,
        population_size: int = DEFAULT_EVOLUTION_POPULATION_SIZE,
        elite_fraction: float = DEFAULT_EVOLUTION_ELITE_FRACTION,
        initial_mutation_probability: float = DEFAULT_EVOLUTION_INITIAL_MUTATION_PROBABILITY,
        mutation_probability: float = DEFAULT_EVOLUTION_MUTATION_PROBABILITY,
        failure_cost: float = math.exp(5.0) * 10.0,
        instance_id: str = "whole-workload-v0",
        evaluation_seed: int = 0,
        repetition: int = 0,
        budget: float | None = None,
    ):
        _validate_search_parameter_space(parameter_space)
        _validate_trial_coordinates(instance_id, evaluation_seed, repetition, budget)
        _validate_evolution_parameters(
            population_size,
            elite_fraction,
            initial_mutation_probability,
            mutation_probability,
            failure_cost,
        )
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise SearchError("evolution seed must be an integer")
        self._parameter_space = dict(parameter_space)
        self._parameter_space_hash = sha256_json(parameter_space)
        self._seed = seed
        self._population_size = population_size
        self._elite_fraction = float(elite_fraction)
        self._initial_mutation_probability = float(initial_mutation_probability)
        self._mutation_probability = float(mutation_probability)
        self._failure_cost = float(failure_cost)
        self._instance_id = instance_id
        self._evaluation_seed = evaluation_seed
        self._repetition = repetition
        self._budget = budget
        self._generation = 0
        self._next_proposal_index = 0
        self._observation_count = 0
        self._parents: list[dict[str, Any]] = []
        self._generation_observations: list[dict[str, Any]] = []
        self._pending: dict[str, dict[str, Any]] = {}

    def ask(self) -> TrialRequest:
        proposal_index = self._next_proposal_index
        self._next_proposal_index += 1
        generator = _proposal_generator(
            "evolution-search-v0",
            self._seed,
            self._parameter_space_hash,
            proposal_index,
            self._generation,
        )
        domains = self._parameter_space["options"]
        for _ in range(1_000):
            if self._parents:
                parent = self._parents[generator.randrange(len(self._parents))]
                proposed = {
                    name: parent["options"].get(name, domain["default"])
                    for name, domain in sorted(domains.items())
                }
                mutation_probability = self._mutation_probability
            else:
                proposed = {
                    name: domain["default"] for name, domain in sorted(domains.items())
                }
                mutation_probability = self._initial_mutation_probability

            for name, domain in sorted(domains.items()):
                if generator.random() < mutation_probability:
                    current = proposed[name]
                    proposed[name] = _sample_value_different_from(
                        generator, name, domain, current
                    )
            active = active_option_names(self._parameter_space, proposed)
            options = {
                name: value
                for name, value in proposed.items()
                if name in active and value != domains[name]["default"]
            }
            identity = {
                "parameterSpaceId": self._parameter_space["parameterSpaceId"],
                "targetSolver": "cvc5",
                "options": options,
            }
            candidate = {
                "schemaVersion": CANDIDATE_SCHEMA_VERSION,
                "candidateId": f"cvc5-evolution-v0-{sha256_json(identity)[:20]}",
                **identity,
            }
            try:
                normalize_candidate(candidate, self._parameter_space)
                break
            except EvaluatorError:
                continue
        else:
            raise SearchError("failed to evolve a constraint-valid candidate")
        request_id = _trial_request_id(
            candidate["candidateId"],
            self._instance_id,
            self._evaluation_seed,
            self._repetition,
            self._budget,
        )
        request = TrialRequest(
            request_id=request_id,
            proposal_index=proposal_index,
            candidate=candidate,
            instance_id=self._instance_id,
            seed=self._evaluation_seed,
            repetition=self._repetition,
            budget=self._budget,
        )
        self._pending[request_id] = candidate
        return request

    def tell(self, result: TrialResult) -> None:
        _validate_trial_result(result)
        candidate = self._pending.pop(result.request_id, None)
        if candidate is None or candidate["candidateId"] != result.candidate_id:
            raise SearchError("evolution result has no matching pending proposal")
        cost = float(result.cost) if result.eligible else self._failure_cost
        self._generation_observations.append(
            {
                "candidate": candidate,
                "cost": cost,
                "status": result.status,
                "correctness": result.correctness,
            }
        )
        self._observation_count += 1
        if len(self._generation_observations) >= self._population_size:
            elite_count = max(
                1, math.ceil(self._population_size * self._elite_fraction)
            )
            ranked = sorted(
                self._generation_observations,
                key=lambda item: (item["cost"], item["candidate"]["candidateId"]),
            )
            self._parents = [dict(item["candidate"]) for item in ranked[:elite_count]]
            self._generation_observations = []
            self._generation += 1

    def reject(self, request: TrialRequest) -> None:
        _validate_trial_request(request)
        self._pending.pop(request.request_id, None)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": EVOLUTION_STRATEGY_STATE_SCHEMA_VERSION,
            "strategy": "evolution-search-v0",
            "seed": self._seed,
            "samplingPolicy": EVOLUTION_SAMPLING_POLICY,
            "populationSize": self._population_size,
            "eliteFraction": self._elite_fraction,
            "initialMutationProbability": self._initial_mutation_probability,
            "mutationProbability": self._mutation_probability,
            "failureCost": self._failure_cost,
            "parameterSpaceSha256": self._parameter_space_hash,
            "instanceId": self._instance_id,
            "evaluationSeed": self._evaluation_seed,
            "repetition": self._repetition,
            "budget": self._budget,
            "generation": self._generation,
            "nextProposalIndex": self._next_proposal_index,
            "observationCount": self._observation_count,
            "parents": self._parents,
            "generationObservations": self._generation_observations,
            "pendingProposals": [
                {"requestId": request_id, "candidate": candidate}
                for request_id, candidate in sorted(self._pending.items())
            ],
        }

    def restore(self, state: Mapping[str, Any]) -> None:
        _validate_evolution_strategy_state(state)
        expected = {
            "schemaVersion": EVOLUTION_STRATEGY_STATE_SCHEMA_VERSION,
            "strategy": "evolution-search-v0",
            "seed": self._seed,
            "samplingPolicy": EVOLUTION_SAMPLING_POLICY,
            "populationSize": self._population_size,
            "eliteFraction": self._elite_fraction,
            "initialMutationProbability": self._initial_mutation_probability,
            "mutationProbability": self._mutation_probability,
            "failureCost": self._failure_cost,
            "parameterSpaceSha256": self._parameter_space_hash,
            "instanceId": self._instance_id,
            "evaluationSeed": self._evaluation_seed,
            "repetition": self._repetition,
            "budget": self._budget,
        }
        for key, value in expected.items():
            if state.get(key) != value:
                raise SearchError(f"incompatible evolution checkpoint field: {key}")
        self._generation = state["generation"]
        self._next_proposal_index = state["nextProposalIndex"]
        self._observation_count = state["observationCount"]
        self._parents = [dict(candidate) for candidate in state["parents"]]
        self._generation_observations = [
            dict(observation) for observation in state["generationObservations"]
        ]
        self._pending = {
            item["requestId"]: dict(item["candidate"])
            for item in state["pendingProposals"]
        }


def _proposal_generator(
    strategy: str,
    seed: int,
    parameter_space_hash: str,
    proposal_index: int,
    generation: int = 0,
) -> random.Random:
    digest = hashlib.sha256(
        canonical_json(
            {
                "strategy": strategy,
                "seed": seed,
                "parameterSpaceSha256": parameter_space_hash,
                "proposalIndex": proposal_index,
                "generation": generation,
            }
        )
    ).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _sample_value_different_from(
    generator: random.Random, name: str, domain: Any, current: Any
) -> Any:
    if domain["kind"] == "boolean":
        return not current
    for _ in range(100):
        sampled = _sample_domain(generator, name, domain)
        if sampled != current:
            return sampled
    return current


def _validate_evolution_parameters(
    population_size: Any,
    elite_fraction: Any,
    initial_mutation_probability: Any,
    mutation_probability: Any,
    failure_cost: Any,
) -> None:
    if (
        isinstance(population_size, bool)
        or not isinstance(population_size, int)
        or population_size < 2
    ):
        raise SearchError("evolution population size must be at least 2")
    if (
        isinstance(elite_fraction, bool)
        or not isinstance(elite_fraction, (int, float))
        or not math.isfinite(float(elite_fraction))
        or elite_fraction <= 0
        or elite_fraction > 1
    ):
        raise SearchError("evolution elite fraction must be in (0, 1]")
    _validate_mutation_probability(initial_mutation_probability)
    _validate_mutation_probability(mutation_probability)
    if (
        isinstance(failure_cost, bool)
        or not isinstance(failure_cost, (int, float))
        or not math.isfinite(float(failure_cost))
        or failure_cost <= 0
    ):
        raise SearchError("evolution failure cost must be finite and positive")


def _validate_mutation_probability(value: Any) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
        or value > 1
    ):
        raise SearchError("random mutation probability must be in (0, 1]")


def _claim_real_evaluation_number(
    state: dict[str, Any],
    pending: Mapping[str, Any],
) -> int:
    """Count one logical candidate once, regardless of process restarts."""

    if pending.get("attemptState") is None:
        state["actualEvaluationCount"] += 1
    return state["actualEvaluationCount"]


def _validate_trial_coordinates(
    instance_id: Any,
    seed: Any,
    repetition: Any,
    budget: Any,
) -> None:
    if not isinstance(instance_id, str) or not instance_id:
        raise SearchError("trial instance id must not be empty")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SearchError("trial seed must be an integer")
    if isinstance(repetition, bool) or not isinstance(repetition, int) or repetition < 0:
        raise SearchError("trial repetition must be a non-negative integer")
    if budget is not None and (
        isinstance(budget, bool)
        or not isinstance(budget, (int, float))
        or not math.isfinite(float(budget))
        or budget <= 0
    ):
        raise SearchError("trial budget must be positive or null")


def _trial_request_id(
    candidate_id: str,
    instance_id: str,
    seed: int,
    repetition: int,
    budget: float | None,
) -> str:
    identity = {
        "candidateId": candidate_id,
        "instanceId": instance_id,
        "seed": seed,
        "repetition": repetition,
        "budget": budget,
    }
    return f"cvc5-trial-v0-{sha256_json(identity)[:20]}"


def _validate_trial_request(request: TrialRequest) -> None:
    if (
        isinstance(request.proposal_index, bool)
        or not isinstance(request.proposal_index, int)
        or request.proposal_index < 0
    ):
        raise SearchError("trial proposal index must be a non-negative integer")
    _validate_trial_coordinates(
        request.instance_id,
        request.seed,
        request.repetition,
        request.budget,
    )
    try:
        candidate = normalize_candidate(request.candidate)
    except EvaluatorError as failure:
        raise SearchError(f"trial request has invalid candidate: {failure}") from failure
    expected = _trial_request_id(
        candidate["candidateId"],
        request.instance_id,
        request.seed,
        request.repetition,
        request.budget,
    )
    if request.request_id != expected:
        raise SearchError("trial request id does not match its coordinates")


def _validate_trial_result(result: TrialResult) -> None:
    for label, value in (
        ("request id", result.request_id),
        ("candidate id", result.candidate_id),
        ("instance id", result.instance_id),
        ("status", result.status),
    ):
        if not isinstance(value, str) or not value:
            raise SearchError(f"trial {label} must not be empty")
    _validate_trial_coordinates(
        result.instance_id,
        result.seed,
        result.repetition,
        result.budget,
    )
    if result.cost is not None and (
        isinstance(result.cost, bool)
        or not isinstance(result.cost, (int, float))
        or not math.isfinite(float(result.cost))
        or result.cost < 0
    ):
        raise SearchError("trial cost must be non-negative or null")
    if (
        isinstance(result.duration_nanos, bool)
        or not isinstance(result.duration_nanos, int)
        or result.duration_nanos < 0
    ):
        raise SearchError("trial duration must be a non-negative integer")
    if not isinstance(result.censored, bool) or not isinstance(result.cache_hit, bool):
        raise SearchError("trial censored and cache_hit flags must be booleans")
    if result.correctness not in {"CORRECT", "INCORRECT", "NOT_ESTABLISHED"}:
        raise SearchError("trial correctness has an unsupported value")
    if result.failure_message is not None and not isinstance(result.failure_message, str):
        raise SearchError("trial failure message must be a string or null")


def _validate_search_parameter_space(parameter_space: Mapping[str, Any]) -> None:
    try:
        validate_parameter_space(parameter_space)
    except EvaluatorError as failure:
        raise SearchError(str(failure)) from failure
    options = parameter_space.get("options")
    if not isinstance(options, dict) or not options:
        raise SearchError("search parameter space must contain options")
    protected = sorted(PROTECTED_CVC5_OPTIONS.intersection(options))
    if protected:
        raise SearchError(
            "search space contains evaluator-controlled options: " + ", ".join(protected)
        )
    for name, domain in options.items():
        _validate_sampleable_domain(name, domain)


def _validate_sampleable_domain(name: str, domain: Any) -> None:
    if not isinstance(domain, dict):
        raise SearchError(f"invalid domain for {name}")
    kind = domain.get("kind")
    if kind == "boolean":
        return
    if kind == "categorical":
        values = domain.get("values")
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
        ):
            raise SearchError(f"categorical domain {name} has no values")
        return
    if kind == "integer":
        minimum = domain.get("minimum")
        maximum = domain.get("maximum")
        step = domain.get("step", 1)
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, int)
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or isinstance(step, bool)
            or not isinstance(step, int)
            or step <= 0
            or minimum > maximum
        ):
            raise SearchError(f"invalid integer domain for {name}")
        if domain.get("sampling", "uniform") == "log-uniform" and minimum <= 0:
            raise SearchError(f"log-uniform integer domain {name} must be positive")
        return
    if kind == "number":
        minimum = domain.get("minimum")
        maximum = domain.get("maximum")
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, (int, float))
            or isinstance(maximum, bool)
            or not isinstance(maximum, (int, float))
            or not math.isfinite(float(minimum))
            or not math.isfinite(float(maximum))
            or minimum > maximum
        ):
            raise SearchError(f"invalid numeric domain for {name}")
        sampling = domain.get("sampling", "uniform")
        if sampling == "log-uniform" and minimum <= 0:
            raise SearchError(f"log-uniform numeric domain {name} must be positive")
        if sampling == "log-uniform-or-zero":
            positive_minimum = domain.get("positiveMinimum")
            if minimum != 0 or not isinstance(positive_minimum, (int, float)) or positive_minimum <= 0:
                raise SearchError(f"invalid log-uniform-or-zero domain for {name}")
        return
    raise SearchError(f"unsupported domain kind for {name}: {kind}")


def _sample_domain(generator: random.Random, name: str, domain: Any) -> Any:
    _validate_sampleable_domain(name, domain)
    kind = domain["kind"]
    if kind == "boolean":
        return bool(generator.getrandbits(1))
    if kind == "categorical":
        return generator.choice(domain["values"])
    if kind == "integer":
        step = domain.get("step", 1)
        count = (domain["maximum"] - domain["minimum"]) // step + 1
        if domain.get("sampling", "uniform") == "log-uniform":
            raw = math.exp(
                generator.uniform(math.log(domain["minimum"]), math.log(domain["maximum"]))
            )
            index = round((raw - domain["minimum"]) / step)
            return domain["minimum"] + step * min(max(index, 0), count - 1)
        return domain["minimum"] + step * generator.randrange(count)
    if domain["minimum"] == domain["maximum"]:
        return float(domain["minimum"])
    sampling = domain.get("sampling", "uniform")
    if sampling == "log-uniform":
        return math.exp(
            generator.uniform(math.log(domain["minimum"]), math.log(domain["maximum"]))
        )
    if sampling == "log-uniform-or-zero":
        if generator.randrange(10) == 0:
            return 0.0
        return math.exp(
            generator.uniform(
                math.log(domain["positiveMinimum"]), math.log(domain["maximum"])
            )
        )
    return generator.uniform(domain["minimum"], domain["maximum"])


def _sample_non_default_domain(
    generator: random.Random,
    name: str,
    domain: Any,
) -> Any:
    """Sample a non-default value when the domain contains one.

    The mutation decision is separate from the value distribution.  This keeps
    the full reviewed parameter space reachable without making an ordinary
    proposal flip roughly half of all Boolean and categorical knobs at once.
    """

    default = domain["default"]
    kind = domain["kind"]
    if kind == "boolean":
        return not default
    if kind == "categorical":
        alternatives = [value for value in domain["values"] if value != default]
        return generator.choice(alternatives) if alternatives else default
    if kind == "integer":
        step = domain.get("step", 1)
        count = (domain["maximum"] - domain["minimum"]) // step + 1
        default_index = (default - domain["minimum"]) // step
        if count <= 1:
            return default
        sampled_index = generator.randrange(count - 1)
        if sampled_index >= default_index:
            sampled_index += 1
        return domain["minimum"] + step * sampled_index
    if domain["minimum"] == domain["maximum"]:
        return default
    if domain.get("sampling", "uniform") == "log-uniform-or-zero" and default == 0:
        return math.exp(
            generator.uniform(
                math.log(domain["positiveMinimum"]), math.log(domain["maximum"])
            )
        )
    for _ in range(100):
        sampled = _sample_domain(generator, name, domain)
        if sampled != default:
            return sampled
    return default


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as destination:
            json.dump(value, destination, ensure_ascii=False, indent=2, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class FileSearchStore:
    """Small auditable run store with no database or background service."""

    def __init__(self, run_directory: Path):
        self.run_directory = run_directory.resolve()
        self.manifest_path = self.run_directory / "manifest.json"
        self.events_path = self.run_directory / "events.jsonl"
        self.state_path = self.run_directory / "state.json"
        self.trials_directory = self.run_directory / "trials"
        self._next_event_sequence = 0

    def open_or_create(
        self, experiment: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        if self.manifest_path.exists():
            manifest = load_json(self.manifest_path)
            self._validate_manifest(manifest)
            if manifest.get("experiment") != experiment:
                raise SearchError(
                    "existing run manifest differs from the requested experiment"
                )
            if not self.state_path.is_file():
                raise SearchError("existing run has no state checkpoint")
            state = load_json(self.state_path)
            self._validate_state(state, manifest["runId"])
            self._next_event_sequence = _repair_and_validate_event_log(self.events_path)
            return manifest, state, False

        if self.run_directory.exists() and any(self.run_directory.iterdir()):
            raise SearchError("new run directory is not empty")
        self.trials_directory.mkdir(parents=True, exist_ok=True)
        run_id = f"cvc5-search-v0-{uuid.uuid4()}"
        manifest = {
            "schemaVersion": SEARCH_MANIFEST_SCHEMA_VERSION,
            "runId": run_id,
            "createdAtEpochMillis": int(time.time() * 1000),
            "experimentSha256": sha256_json(experiment),
            "experiment": dict(experiment),
        }
        state = {
            "schemaVersion": SEARCH_STATE_SCHEMA_VERSION,
            "runId": run_id,
            "status": "RUNNING",
            "activeWallTimeNanos": 0,
            "actualEvaluationCount": 0,
            "completedTrialCount": 0,
            "cacheHitCount": 0,
            "duplicateCount": 0,
            "seenCandidateIds": [],
            "pendingTrial": None,
            "incumbent": None,
            "stopReason": None,
            "strategyState": None,
            "updatedAtEpochMillis": int(time.time() * 1000),
        }
        atomic_write_json(self.manifest_path, manifest)
        self.save_state(state)
        return manifest, state, True

    def append_event(self, event_type: str, **payload: Any) -> int:
        self.run_directory.mkdir(parents=True, exist_ok=True)
        sequence = self._next_event_sequence
        event = {
            "schemaVersion": SEARCH_EVENT_SCHEMA_VERSION,
            "sequence": sequence,
            "atEpochMillis": int(time.time() * 1000),
            "type": event_type,
            "payload": payload,
        }
        _validate_search_event(event, sequence)
        with self.events_path.open("a", encoding="utf-8") as destination:
            destination.write(canonical_json(event).decode("utf-8"))
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        self._next_event_sequence += 1
        return sequence

    def save_state(self, state: Mapping[str, Any]) -> None:
        checkpoint = dict(state)
        checkpoint["updatedAtEpochMillis"] = int(time.time() * 1000)
        self._validate_state(checkpoint, checkpoint.get("runId"))
        atomic_write_json(self.state_path, checkpoint)

    def trial_directory(self, trial_id: str) -> Path:
        directory = self.trials_directory / trial_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _validate_manifest(manifest: Mapping[str, Any]) -> None:
        required = {
            "schemaVersion",
            "runId",
            "createdAtEpochMillis",
            "experimentSha256",
            "experiment",
        }
        if set(manifest) != required:
            raise SearchError("search manifest fields do not match the v0 contract")
        if manifest.get("schemaVersion") != SEARCH_MANIFEST_SCHEMA_VERSION:
            raise SearchError("existing run has an unsupported manifest schema")
        if not isinstance(manifest.get("runId"), str) or not manifest["runId"]:
            raise SearchError("search manifest has invalid runId")
        _search_non_negative_int(manifest, "createdAtEpochMillis")
        experiment = manifest.get("experiment")
        if not isinstance(experiment, dict) or set(experiment) != {
            "strategy",
            "budget",
            "evaluator",
            "objective",
        }:
            raise SearchError("search manifest has invalid experiment")
        _validate_experiment_contract(experiment)
        if manifest.get("experimentSha256") != sha256_json(experiment):
            raise SearchError("search manifest experiment digest mismatch")

    @staticmethod
    def _validate_state(state: Mapping[str, Any], run_id: Any) -> None:
        required = {
            "schemaVersion",
            "runId",
            "status",
            "activeWallTimeNanos",
            "actualEvaluationCount",
            "completedTrialCount",
            "cacheHitCount",
            "duplicateCount",
            "seenCandidateIds",
            "pendingTrial",
            "incumbent",
            "stopReason",
            "strategyState",
            "updatedAtEpochMillis",
        }
        if set(state) != required:
            raise SearchError("search state fields do not match the v0 contract")
        if state.get("schemaVersion") != SEARCH_STATE_SCHEMA_VERSION:
            raise SearchError("unsupported search state schema")
        if not isinstance(run_id, str) or state.get("runId") != run_id:
            raise SearchError("search state belongs to another run")
        for field in (
            "activeWallTimeNanos",
            "actualEvaluationCount",
            "completedTrialCount",
            "cacheHitCount",
            "duplicateCount",
        ):
            value = state.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SearchError(f"invalid state counter: {field}")
        _search_non_negative_int(state, "updatedAtEpochMillis")
        if not isinstance(state.get("seenCandidateIds"), list):
            raise SearchError("seenCandidateIds must be an array")
        seen = state["seenCandidateIds"]
        if (
            any(not isinstance(item, str) or not item for item in seen)
            or len(seen) != len(set(seen))
        ):
            raise SearchError("seenCandidateIds must contain unique non-empty strings")
        if state.get("status") not in {"RUNNING", "INTERRUPTED", "FINISHED"}:
            raise SearchError("search state has invalid status")
        if state.get("stopReason") is not None and not isinstance(
            state.get("stopReason"), str
        ):
            raise SearchError("search state has invalid stopReason")
        pending = state.get("pendingTrial")
        if pending is not None:
            _validate_pending_trial(pending)
            if pending["candidate"]["candidateId"] not in seen:
                raise SearchError("pending trial candidate is absent from seenCandidateIds")
        incumbent = state.get("incumbent")
        if incumbent is not None:
            if not isinstance(incumbent, dict) or set(incumbent) != {
                "candidateId",
                "cost",
                "trialId",
            }:
                raise SearchError("search state has invalid incumbent")
            if not isinstance(incumbent["candidateId"], str) or not incumbent["candidateId"]:
                raise SearchError("search incumbent has invalid candidateId")
            if (
                isinstance(incumbent["cost"], bool)
                or not isinstance(incumbent["cost"], (int, float))
                or not math.isfinite(float(incumbent["cost"]))
                or incumbent["cost"] < 0
            ):
                raise SearchError("search incumbent has invalid cost")
            if not isinstance(incumbent["trialId"], str) or not incumbent["trialId"]:
                raise SearchError("search incumbent has invalid trialId")
            if incumbent["candidateId"] not in seen:
                raise SearchError("search incumbent is absent from seenCandidateIds")
        strategy_state = state.get("strategyState")
        if strategy_state is not None:
            _validate_strategy_state(strategy_state)
        if state["cacheHitCount"] > state["completedTrialCount"]:
            raise SearchError("cacheHitCount exceeds completedTrialCount")
        if state["completedTrialCount"] > len(seen):
            raise SearchError("completedTrialCount exceeds proposed unique candidates")


class FileEvaluationCache:
    """Content-addressed cache; ``entry.json`` is the completion marker."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve()

    def key(self, evaluation_identity: Mapping[str, Any]) -> str:
        return sha256_json(evaluation_identity)

    def get(self, key: str) -> dict[str, Any] | None:
        entry_directory = self.directory / key[:2] / key
        marker = entry_directory / "entry.json"
        if not marker.is_file():
            return None
        entry = load_json(marker)
        if (
            entry.get("schemaVersion") != SEARCH_CACHE_ENTRY_SCHEMA_VERSION
            or entry.get("cacheKey") != key
        ):
            raise SearchError(f"invalid cache entry: {key}")
        required = {
            "schemaVersion",
            "cacheKey",
            "createdAtEpochMillis",
            "evaluationIdentitySha256",
            "candidateSha256",
            "reportSha256",
            "scoreSha256",
        }
        if set(entry) != required:
            raise SearchError(f"cache entry fields do not match the v0 contract: {key}")
        _search_non_negative_int(entry, "createdAtEpochMillis")
        identity_path = entry_directory / "evaluation-identity.json"
        candidate_path = entry_directory / "candidate.json"
        report_path = entry_directory / "report.json"
        score_path = entry_directory / "score.json"
        for field, path in (
            ("candidateSha256", candidate_path),
            ("reportSha256", report_path),
            ("scoreSha256", score_path),
        ):
            if not path.is_file() or sha256_file(path) != entry.get(field):
                raise SearchError(f"cache artifact digest mismatch for {key}: {path.name}")
        identity = load_json(identity_path)
        if sha256_json(identity) != key or entry["evaluationIdentitySha256"] != key:
            raise SearchError(f"cache evaluation identity mismatch: {key}")
        candidate = load_json(candidate_path)
        report = load_json(report_path)
        score = load_json(score_path)
        try:
            normalized = normalize_candidate(candidate)
            validate_report(report, expected_candidate_id=normalized["candidateId"])
            validate_score(score, expected_candidate_id=normalized["candidateId"])
        except EvaluatorError as failure:
            raise SearchError(f"invalid cached evaluator artifact {key}: {failure}") from failure
        if report["candidate"] != normalized:
            raise SearchError(f"cached report candidate mismatch: {key}")
        return {
            "entry": entry,
            "candidate": candidate,
            "report": report,
            "score": score,
        }

    def put(
        self,
        key: str,
        *,
        candidate: Mapping[str, Any],
        report: Mapping[str, Any],
        score: Mapping[str, Any],
        evaluation_identity: Mapping[str, Any],
    ) -> None:
        entry_directory = self.directory / key[:2] / key
        entry_directory.mkdir(parents=True, exist_ok=True)
        if self.key(evaluation_identity) != key:
            raise SearchError("cache key does not match evaluation identity")
        try:
            normalized = normalize_candidate(candidate)
            validate_report(report, expected_candidate_id=normalized["candidateId"])
            validate_score(score, expected_candidate_id=normalized["candidateId"])
        except EvaluatorError as failure:
            raise SearchError(f"refusing to cache invalid evaluator artifact: {failure}") from failure
        identity_path = entry_directory / "evaluation-identity.json"
        candidate_path = entry_directory / "candidate.json"
        report_path = entry_directory / "report.json"
        score_path = entry_directory / "score.json"
        atomic_write_json(identity_path, evaluation_identity)
        atomic_write_json(candidate_path, candidate)
        atomic_write_json(report_path, report)
        atomic_write_json(score_path, score)
        atomic_write_json(
            entry_directory / "entry.json",
            {
                "schemaVersion": SEARCH_CACHE_ENTRY_SCHEMA_VERSION,
                "cacheKey": key,
                "createdAtEpochMillis": int(time.time() * 1000),
                "evaluationIdentitySha256": sha256_json(evaluation_identity),
                "candidateSha256": sha256_file(candidate_path),
                "reportSha256": sha256_file(report_path),
                "scoreSha256": sha256_file(score_path),
            },
        )


class RealCvc5ProfileTrialEvaluator:
    """Execute exactly one profile/repetition coordinate with real cvc5."""

    def __init__(
        self,
        *,
        tsa_cli_jar: Path,
        parameter_space: Mapping[str, Any],
        workload: Mapping[str, Any],
        baseline_report: Mapping[str, Any],
        backend_role: str,
        warmups: int,
        java: str,
        progress_interval_seconds: int,
        stream_child_output: bool,
        timeout_penalty: float,
        reward_clip: float,
        exclude_recorded_failures: bool,
    ):
        validate_workload_manifest(workload)
        self.tsa_cli_jar = tsa_cli_jar
        self.parameter_space = parameter_space
        self.workload = workload
        self.baseline_report = baseline_report
        self.backend_role = backend_role
        self.warmups = warmups
        self.java = java
        self.progress_interval_seconds = progress_interval_seconds
        self.stream_child_output = stream_child_output
        self.timeout_penalty = timeout_penalty
        self.reward_clip = reward_clip
        self.exclude_recorded_failures = exclude_recorded_failures
        self._profiles = {entry["profileId"]: entry for entry in workload["profiles"]}

    def evaluate(
        self,
        request: TrialRequest,
        report_path: Path,
        process_timeout_seconds: float | None,
        process_heartbeat: Callable[[], None] | None = None,
    ) -> ProfileTrialEvaluation:
        entry = self._profile_entry(request)
        report = run_replay(
            tsa_cli_jar=self.tsa_cli_jar,
            candidate=request.candidate,
            output_path=report_path,
            profiles=[Path(entry["path"])],
            backend_role=self.backend_role,
            repetitions=1,
            warmups=self.warmups,
            java=self.java,
            process_timeout_seconds=process_timeout_seconds,
            parameter_space=self.parameter_space,
            stream_child_output=self.stream_child_output,
            progress_interval_seconds=self.progress_interval_seconds,
            process_heartbeat=process_heartbeat,
            process_heartbeat_interval_seconds=PROCESS_HEARTBEAT_INTERVAL_SECONDS,
        )
        return self.score_existing_report(request, report)

    def score_existing_report(
        self,
        request: TrialRequest,
        report: Mapping[str, Any],
    ) -> ProfileTrialEvaluation:
        """Validate and score a completed report recovered before score flush."""

        entry = self._profile_entry(request)
        baseline = select_profile_report(
            self.baseline_report,
            request.instance_id,
            source_file_name=entry["fileName"],
        )
        score = score_reports(
            baseline,
            report,
            timeout_penalty=self.timeout_penalty,
            reward_clip=self.reward_clip,
            exclude_recorded_failures=self.exclude_recorded_failures,
        )
        return self.validate_artifacts(request, report, score)

    def validate_artifacts(
        self,
        request: TrialRequest,
        report: Mapping[str, Any],
        score: Mapping[str, Any],
    ) -> ProfileTrialEvaluation:
        entry = self._profile_entry(request)
        normalized = normalize_candidate(request.candidate, self.parameter_space)
        profile_workload = single_profile_workload(self.workload, request.instance_id)
        validate_report_against_workload(
            report,
            self.parameter_space,
            profile_workload,
            expected_repetitions=1,
            expected_warmups=self.warmups,
        )
        if report["candidate"] != normalized:
            raise EvaluatorError("profile trial report candidate mismatch")
        validate_score(score, expected_candidate_id=request.candidate["candidateId"])
        if score["parameterSpaceId"] != request.candidate["parameterSpaceId"]:
            raise EvaluatorError("profile trial score parameterSpaceId mismatch")
        if score["backendRole"] != self.backend_role:
            raise EvaluatorError("profile trial score backendRole mismatch")
        if len(score["profiles"]) != 1 or score["profiles"][0]["profileId"] != request.instance_id:
            raise EvaluatorError("profile trial score contains another workload instance")
        baseline = select_profile_report(
            self.baseline_report,
            request.instance_id,
            source_file_name=entry["fileName"],
        )
        expected_score = score_reports(
            baseline,
            report,
            timeout_penalty=self.timeout_penalty,
            reward_clip=self.reward_clip,
            exclude_recorded_failures=self.exclude_recorded_failures,
        )
        if score != expected_score:
            raise EvaluatorError("profile trial score does not match its replay report")
        result = _trial_result_from_score(request, report, score, cache_hit=False)
        return ProfileTrialEvaluation(
            request=request,
            report=dict(report),
            score=dict(score),
            result=result,
        )

    def _profile_entry(self, request: TrialRequest) -> Mapping[str, Any]:
        _validate_trial_request(request)
        entry = self._profiles.get(request.instance_id)
        if entry is None:
            raise EvaluatorError(f"unknown profile trial instance: {request.instance_id}")
        if request.repetition < 0:
            raise EvaluatorError("profile trial repetition must be non-negative")
        if request.candidate.get("parameterSpaceId") != self.parameter_space["parameterSpaceId"]:
            raise EvaluatorError("profile trial candidate uses another parameter space")
        return entry


class RealCvc5ReplayEvaluator:
    """Aggregate real one-profile trials into one whole-workload evaluation."""

    def __init__(
        self,
        *,
        tsa_cli_jar: Path,
        parameter_space: Mapping[str, Any],
        workload: Mapping[str, Any],
        baseline_report: Mapping[str, Any],
        profile_directory: Path | None,
        profiles: Sequence[Path],
        backend_role: str,
        repetitions: int,
        warmups: int,
        java: str,
        progress_interval_seconds: int,
        stream_child_output: bool,
        timeout_penalty: float,
        reward_clip: float,
        exclude_recorded_failures: bool,
    ):
        self.tsa_cli_jar = tsa_cli_jar
        self.parameter_space = parameter_space
        self.workload = workload
        self.baseline_report = baseline_report
        self.profile_directory = profile_directory
        self.profiles = profiles
        self.backend_role = backend_role
        self.repetitions = repetitions
        self.warmups = warmups
        self.java = java
        self.progress_interval_seconds = progress_interval_seconds
        self.stream_child_output = stream_child_output
        self.timeout_penalty = timeout_penalty
        self.reward_clip = reward_clip
        self.exclude_recorded_failures = exclude_recorded_failures
        self.profile_trial_evaluator = RealCvc5ProfileTrialEvaluator(
            tsa_cli_jar=tsa_cli_jar,
            parameter_space=parameter_space,
            workload=workload,
            baseline_report=baseline_report,
            backend_role=backend_role,
            warmups=warmups,
            java=java,
            progress_interval_seconds=progress_interval_seconds,
            stream_child_output=stream_child_output,
            timeout_penalty=timeout_penalty,
            reward_clip=reward_clip,
            exclude_recorded_failures=exclude_recorded_failures,
        )

    def evaluate(
        self,
        request: TrialRequest,
        report_path: Path,
        process_timeout_seconds: float | None,
        process_heartbeat: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], TrialResult]:
        _validate_trial_request(request)
        candidate = request.candidate
        started_nanos = time.monotonic_ns()
        started = started_nanos / 1_000_000_000
        profile_trials: list[tuple[str, int, Mapping[str, Any]]] = []
        trial_root = report_path.parent / "profile-trials"
        for profile_index, entry in enumerate(self.workload["profiles"]):
            profile_id = entry["profileId"]
            for repetition in range(self.repetitions):
                child_request = TrialRequest(
                    request_id=_trial_request_id(
                        candidate["candidateId"],
                        profile_id,
                        request.seed,
                        repetition,
                        request.budget,
                    ),
                    proposal_index=request.proposal_index,
                    candidate=dict(candidate),
                    instance_id=profile_id,
                    seed=request.seed,
                    repetition=repetition,
                    budget=request.budget,
                )
                child_directory = trial_root / (
                    f"{profile_index:04d}-{sha256_json(profile_id)[:12]}-r{repetition:04d}"
                )
                child_report_path = child_directory / "report.json"
                child_score_path = child_directory / "score.json"
                if child_report_path.is_file() and child_score_path.is_file():
                    evaluation = self.profile_trial_evaluator.validate_artifacts(
                        child_request,
                        load_json(child_report_path),
                        load_json(child_score_path),
                    )
                else:
                    remaining = None
                    if process_timeout_seconds is not None:
                        remaining = process_timeout_seconds - (time.monotonic() - started)
                        if remaining <= 0:
                            raise ReplayTimeoutError(process_timeout_seconds)
                    evaluation = self.profile_trial_evaluator.evaluate(
                        child_request,
                        child_report_path,
                        remaining,
                        process_heartbeat,
                    )
                    atomic_write_json(child_score_path, evaluation.score)
                    atomic_write_json(child_directory / "result.json", asdict(evaluation.result))
                profile_trials.append((profile_id, repetition, evaluation.report))
                if not evaluation.result.eligible:
                    raise CandidateDisqualifiedError(
                        request,
                        evaluation.result,
                        time.monotonic_ns() - started_nanos,
                    )

        profile_file_names = {
            entry["profileId"]: entry["fileName"] for entry in self.workload["profiles"]
        }
        report = aggregate_profile_trial_reports(
            candidate=candidate,
            backend_role=self.backend_role,
            repetitions=self.repetitions,
            warmups=self.warmups,
            profile_file_names=profile_file_names,
            trials=profile_trials,
        )
        atomic_write_json(report_path, report)
        score = score_reports(
            self.baseline_report,
            report,
            timeout_penalty=self.timeout_penalty,
            reward_clip=self.reward_clip,
            exclude_recorded_failures=self.exclude_recorded_failures,
        )
        self.validate_artifacts(candidate, report, score)
        result = _trial_result_from_score(request, report, score, cache_hit=False)
        return report, score, result

    def validate_artifacts(
        self,
        candidate: Mapping[str, Any],
        report: Mapping[str, Any],
        score: Mapping[str, Any],
    ) -> None:
        normalized = normalize_candidate(candidate, self.parameter_space)
        validate_report_against_workload(
            report,
            self.parameter_space,
            self.workload,
            expected_repetitions=self.repetitions,
            expected_warmups=self.warmups,
        )
        if report["candidate"] != normalized:
            raise EvaluatorError("replay report candidate does not match proposed candidate")
        validate_score(score, expected_candidate_id=candidate["candidateId"])
        if score["parameterSpaceId"] != candidate["parameterSpaceId"]:
            raise EvaluatorError("score parameterSpaceId does not match candidate")
        if score["backendRole"] != self.backend_role:
            raise EvaluatorError("score backendRole does not match evaluator")


def _trial_result_from_score(
    request: TrialRequest,
    report: Mapping[str, Any],
    score: Mapping[str, Any],
    *,
    cache_hit: bool,
) -> TrialResult:
    totals = score["totals"]
    status = (
        "SUCCESS"
        if score["eligible"]
        else "WRONG_ANSWER"
        if totals["wrongAnswerCount"]
        else "KNOWN_STATUS_TIMEOUT"
        if totals["knownStatusTimeoutCount"]
        else "REPLAY_FAILURE"
        if totals["failedReplayCount"]
        else "INELIGIBLE"
    )
    correctness = (
        "INCORRECT"
        if totals["wrongAnswerCount"]
        else "CORRECT"
        if score["eligible"]
        else "NOT_ESTABLISHED"
    )
    duration_nanos = sum(result["wallDurationNanos"] for result in report["results"])
    result = TrialResult(
        request_id=request.request_id,
        candidate_id=request.candidate["candidateId"],
        instance_id=request.instance_id,
        seed=request.seed,
        repetition=request.repetition,
        budget=request.budget,
        status=status,
        cost=math.exp(-float(score["reward"])) if score["eligible"] else None,
        duration_nanos=duration_nanos,
        censored=bool(totals["knownStatusTimeoutCount"]),
        correctness=correctness,
        cache_hit=cache_hit,
    )
    _validate_trial_result(result)
    return result


def _failed_trial_result(
    request: TrialRequest,
    *,
    status: str,
    duration_nanos: int,
    censored: bool = False,
    correctness: str = "NOT_ESTABLISHED",
    failure_message: str | None = None,
) -> TrialResult:
    result = TrialResult(
        request_id=request.request_id,
        candidate_id=request.candidate["candidateId"],
        instance_id=request.instance_id,
        seed=request.seed,
        repetition=request.repetition,
        budget=request.budget,
        status=status,
        cost=None,
        duration_nanos=duration_nanos,
        censored=censored,
        correctness=correctness,
        cache_hit=False,
        failure_message=failure_message,
    )
    _validate_trial_result(result)
    return result


def _repair_and_validate_event_log(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("rb+") as stream:
        records = stream.readlines()
        valid_count = 0
        valid_bytes = 0
        for index, raw in enumerate(records):
            if not raw.strip():
                if index == len(records) - 1:
                    break
                raise SearchError("event log contains a blank record")
            try:
                event = json.loads(raw.decode("utf-8"))
                _validate_search_event(event, valid_count)
            except (UnicodeDecodeError, json.JSONDecodeError, SearchError) as failure:
                if index != len(records) - 1:
                    raise SearchError(f"event log is corrupt at record {index}: {failure}") from failure
                stream.seek(valid_bytes)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                return valid_count
            valid_count += 1
            valid_bytes += len(raw)
        if valid_bytes != sum(len(raw) for raw in records):
            stream.seek(valid_bytes)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        return valid_count


def _validate_search_event(event: Any, expected_sequence: int) -> None:
    required = {"schemaVersion", "sequence", "atEpochMillis", "type", "payload"}
    if not isinstance(event, dict) or set(event) != required:
        raise SearchError("event fields do not match the v0 contract")
    if event.get("schemaVersion") != SEARCH_EVENT_SCHEMA_VERSION:
        raise SearchError("event has unsupported schemaVersion")
    if event.get("sequence") != expected_sequence:
        raise SearchError("event sequence is not contiguous")
    _search_non_negative_int(event, "atEpochMillis")
    if event.get("type") not in SEARCH_EVENT_TYPES:
        raise SearchError("event has invalid type")
    if not isinstance(event.get("payload"), dict):
        raise SearchError("event payload must be an object")


def _search_non_negative_int(value: Mapping[str, Any], key: str) -> int:
    field = value.get(key)
    if isinstance(field, bool) or not isinstance(field, int) or field < 0:
        raise SearchError(f"{key} must be a non-negative integer")
    return field


def _validate_pending_trial(value: Any) -> None:
    required = {
        "trialId",
        "requestId",
        "proposalIndex",
        "candidate",
        "instanceId",
        "evaluationSeed",
        "repetition",
        "budget",
        "cacheKey",
        "evaluationIdentity",
        "proposedAtEpochMillis",
    }
    optional = {
        "attemptState",
        "attemptStartedAtEpochMillis",
        "attemptLastHeartbeatEpochMillis",
    }
    if not isinstance(value, dict) or not required.issubset(value) or set(value).difference(
        required | optional
    ):
        raise SearchError("pending trial fields do not match the v0 contract")
    for field in ("trialId", "requestId", "instanceId", "cacheKey"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise SearchError(f"pending trial has invalid {field}")
    if not _is_search_sha256(value["cacheKey"]):
        raise SearchError("pending trial has invalid cacheKey")
    _search_non_negative_int(value, "proposalIndex")
    _search_non_negative_int(value, "repetition")
    _search_non_negative_int(value, "proposedAtEpochMillis")
    _validate_trial_coordinates(
        value["instanceId"],
        value.get("evaluationSeed"),
        value["repetition"],
        value["budget"],
    )
    if not isinstance(value.get("candidate"), dict) or not isinstance(
        value.get("evaluationIdentity"), dict
    ):
        raise SearchError("pending trial has invalid candidate or evaluation identity")
    try:
        normalize_candidate(value["candidate"])
    except EvaluatorError as failure:
        raise SearchError(f"pending trial has invalid candidate: {failure}") from failure
    if value["evaluationIdentity"].get("candidate") != value["candidate"]:
        raise SearchError("pending trial evaluation identity candidate mismatch")
    expected_request_id = _trial_request_id(
        value["candidate"]["candidateId"],
        value["instanceId"],
        value["evaluationSeed"],
        value["repetition"],
        value["budget"],
    )
    if value["requestId"] != expected_request_id:
        raise SearchError("pending trial request id mismatch")
    _validate_evaluation_identity(value["evaluationIdentity"])
    if sha256_json(value["evaluationIdentity"]) != value["cacheKey"]:
        raise SearchError("pending trial cache key mismatch")
    state = value.get("attemptState")
    if state is not None and state not in {"RUNNING", "INTERRUPTED", "RECOVERED"}:
        raise SearchError("pending trial has invalid attemptState")
    for field in ("attemptStartedAtEpochMillis", "attemptLastHeartbeatEpochMillis"):
        if field in value:
            _search_non_negative_int(value, field)
    has_started = "attemptStartedAtEpochMillis" in value
    has_heartbeat = "attemptLastHeartbeatEpochMillis" in value
    if state is None and (has_started or has_heartbeat):
        raise SearchError("pending trial has timestamps without an attempt state")
    if state is not None and not (has_started and has_heartbeat):
        raise SearchError("pending trial attempt has incomplete timestamps")
    if has_started and value["attemptLastHeartbeatEpochMillis"] < value["attemptStartedAtEpochMillis"]:
        raise SearchError("pending trial heartbeat precedes attempt start")


def _validate_strategy_state(value: Any) -> None:
    if isinstance(value, dict) and value.get("strategy") == "evolution-search-v0":
        _validate_evolution_strategy_state(value)
        return
    required = {
        "schemaVersion",
        "strategy",
        "seed",
        "samplingPolicy",
        "mutationProbability",
        "parameterSpaceSha256",
        "instanceId",
        "evaluationSeed",
        "repetition",
        "budget",
        "nextProposalIndex",
        "observationCount",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise SearchError("strategy state fields do not match the v0 contract")
    if value.get("schemaVersion") != SEARCH_STRATEGY_STATE_SCHEMA_VERSION:
        raise SearchError("strategy state has unsupported schemaVersion")
    if value.get("strategy") != "random-search-v0":
        raise SearchError("strategy state has unsupported strategy")
    if value.get("samplingPolicy") != RANDOM_SAMPLING_POLICY:
        raise SearchError("strategy state has unsupported sampling policy")
    _validate_mutation_probability(value.get("mutationProbability"))
    if isinstance(value.get("seed"), bool) or not isinstance(value.get("seed"), int):
        raise SearchError("strategy state has invalid seed")
    if not _is_search_sha256(value.get("parameterSpaceSha256")):
        raise SearchError("strategy state has invalid parameterSpaceSha256")
    _validate_trial_coordinates(
        value.get("instanceId"),
        value.get("evaluationSeed"),
        value.get("repetition"),
        value.get("budget"),
    )
    _search_non_negative_int(value, "nextProposalIndex")
    _search_non_negative_int(value, "observationCount")
    if value["observationCount"] > value["nextProposalIndex"]:
        raise SearchError("strategy observations exceed proposed candidates")


def _validate_evolution_strategy_state(value: Any) -> None:
    required = {
        "schemaVersion",
        "strategy",
        "seed",
        "samplingPolicy",
        "populationSize",
        "eliteFraction",
        "initialMutationProbability",
        "mutationProbability",
        "failureCost",
        "parameterSpaceSha256",
        "instanceId",
        "evaluationSeed",
        "repetition",
        "budget",
        "generation",
        "nextProposalIndex",
        "observationCount",
        "parents",
        "generationObservations",
        "pendingProposals",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise SearchError("evolution strategy state fields do not match the v0 contract")
    if value.get("schemaVersion") != EVOLUTION_STRATEGY_STATE_SCHEMA_VERSION:
        raise SearchError("evolution strategy state has unsupported schemaVersion")
    if value.get("strategy") != "evolution-search-v0":
        raise SearchError("evolution strategy state has unsupported strategy")
    if value.get("samplingPolicy") != EVOLUTION_SAMPLING_POLICY:
        raise SearchError("evolution strategy state has unsupported sampling policy")
    _validate_evolution_parameters(
        value.get("populationSize"),
        value.get("eliteFraction"),
        value.get("initialMutationProbability"),
        value.get("mutationProbability"),
        value.get("failureCost"),
    )
    if isinstance(value.get("seed"), bool) or not isinstance(value.get("seed"), int):
        raise SearchError("evolution strategy state has invalid seed")
    if not _is_search_sha256(value.get("parameterSpaceSha256")):
        raise SearchError("evolution strategy state has invalid parameterSpaceSha256")
    _validate_trial_coordinates(
        value.get("instanceId"),
        value.get("evaluationSeed"),
        value.get("repetition"),
        value.get("budget"),
    )
    for field in ("generation", "nextProposalIndex", "observationCount"):
        _search_non_negative_int(value, field)
    for field in ("parents", "generationObservations", "pendingProposals"):
        if not isinstance(value.get(field), list):
            raise SearchError(f"evolution strategy state {field} must be an array")
    if len(value["generationObservations"]) >= value["populationSize"]:
        raise SearchError("evolution checkpoint contains an unselected full population")
    elite_limit = max(1, math.ceil(value["populationSize"] * value["eliteFraction"]))
    if len(value["parents"]) > elite_limit:
        raise SearchError("evolution checkpoint contains too many elite parents")
    for candidate in value["parents"]:
        try:
            normalize_candidate(candidate)
        except EvaluatorError as failure:
            raise SearchError("evolution checkpoint has an invalid parent") from failure
    for observation in value["generationObservations"]:
        if not isinstance(observation, dict) or set(observation) != {
            "candidate",
            "cost",
            "status",
            "correctness",
        }:
            raise SearchError("evolution observation fields do not match v0")
        try:
            normalize_candidate(observation["candidate"])
        except EvaluatorError as failure:
            raise SearchError("evolution checkpoint has an invalid observation") from failure
        cost = observation["cost"]
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(float(cost))
            or cost < 0
        ):
            raise SearchError("evolution observation has invalid cost")
        if not isinstance(observation["status"], str) or not observation["status"]:
            raise SearchError("evolution observation has invalid status")
        if observation["correctness"] not in {
            "CORRECT",
            "INCORRECT",
            "NOT_ESTABLISHED",
        }:
            raise SearchError("evolution observation has invalid correctness")
    pending_ids: set[str] = set()
    for pending in value["pendingProposals"]:
        if not isinstance(pending, dict) or set(pending) != {"requestId", "candidate"}:
            raise SearchError("evolution pending proposal fields do not match v0")
        request_id = pending["requestId"]
        if not isinstance(request_id, str) or not request_id or request_id in pending_ids:
            raise SearchError("evolution checkpoint has duplicate pending request")
        pending_ids.add(request_id)
        try:
            candidate = normalize_candidate(pending["candidate"])
        except EvaluatorError as failure:
            raise SearchError("evolution checkpoint has an invalid pending candidate") from failure
        expected = _trial_request_id(
            candidate["candidateId"],
            value["instanceId"],
            value["evaluationSeed"],
            value["repetition"],
            value["budget"],
        )
        if request_id != expected:
            raise SearchError("evolution pending request id mismatch")
    if value["observationCount"] + len(pending_ids) > value["nextProposalIndex"]:
        raise SearchError("evolution checkpoint counts exceed proposed candidates")


def _trial_request_from_pending(value: Mapping[str, Any]) -> TrialRequest:
    return TrialRequest(
        request_id=value["requestId"],
        proposal_index=value["proposalIndex"],
        candidate=dict(value["candidate"]),
        instance_id=value["instanceId"],
        seed=value["evaluationSeed"],
        repetition=value["repetition"],
        budget=value["budget"],
    )


def _validate_scoring_policy(value: Any, label: str) -> None:
    required = {"timeoutPenalty", "rewardClip", "excludeRecordedFailures"}
    if not isinstance(value, dict) or set(value) != required:
        raise SearchError(f"{label} fields do not match the v0 contract")
    timeout_penalty = value.get("timeoutPenalty")
    reward_clip = value.get("rewardClip")
    if (
        isinstance(timeout_penalty, bool)
        or not isinstance(timeout_penalty, (int, float))
        or not math.isfinite(float(timeout_penalty))
        or timeout_penalty < 1
    ):
        raise SearchError(f"{label} has invalid timeoutPenalty")
    if (
        isinstance(reward_clip, bool)
        or not isinstance(reward_clip, (int, float))
        or not math.isfinite(float(reward_clip))
        or reward_clip <= 0
    ):
        raise SearchError(f"{label} has invalid rewardClip")
    if not isinstance(value.get("excludeRecordedFailures"), bool):
        raise SearchError(f"{label} has invalid excludeRecordedFailures")


def _validate_host_identity(value: Any, label: str) -> None:
    required = {"system", "release", "machine", "node"}
    if not isinstance(value, dict) or set(value) != required:
        raise SearchError(f"{label} fields do not match the v0 contract")
    if any(not isinstance(value[field], str) for field in required):
        raise SearchError(f"{label} fields must be strings")


def _validate_evaluation_identity(value: Any) -> None:
    required = {
        "evaluatorVersion",
        "searchSeed",
        "requestId",
        "instanceId",
        "evaluationSeed",
        "repetition",
        "budget",
        "host",
        "candidate",
        "parameterSpaceSha256",
        "baselineSha256",
        "tsaCliJarSha256",
        "workloadSha256",
        "backendRole",
        "repetitions",
        "warmups",
        "scoringPolicy",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise SearchError("evaluation identity fields do not match the v0 contract")
    if value.get("evaluatorVersion") != SEARCH_EVALUATOR_VERSION:
        raise SearchError("evaluation identity has unsupported evaluatorVersion")
    if isinstance(value.get("searchSeed"), bool) or not isinstance(value.get("searchSeed"), int):
        raise SearchError("evaluation identity has invalid searchSeed")
    if not isinstance(value.get("requestId"), str) or not value["requestId"]:
        raise SearchError("evaluation identity has invalid requestId")
    _validate_trial_coordinates(
        value.get("instanceId"),
        value.get("evaluationSeed"),
        value.get("repetition"),
        value.get("budget"),
    )
    _validate_host_identity(value.get("host"), "evaluation identity host")
    candidate = value.get("candidate")
    if not isinstance(candidate, dict):
        raise SearchError("evaluation identity has invalid candidate")
    try:
        normalize_candidate(candidate)
    except EvaluatorError as failure:
        raise SearchError(f"evaluation identity has invalid candidate: {failure}") from failure
    expected_request_id = _trial_request_id(
        candidate["candidateId"],
        value["instanceId"],
        value["evaluationSeed"],
        value["repetition"],
        value["budget"],
    )
    if value["requestId"] != expected_request_id:
        raise SearchError("evaluation identity request id mismatch")
    for field in (
        "parameterSpaceSha256",
        "baselineSha256",
        "tsaCliJarSha256",
        "workloadSha256",
    ):
        if not _is_search_sha256(value.get(field)):
            raise SearchError(f"evaluation identity has invalid {field}")
    if value.get("backendRole") not in {"regular-bv", "integer-blasted"}:
        raise SearchError("evaluation identity has invalid backendRole")
    repetitions = value.get("repetitions")
    warmups = value.get("warmups")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions <= 0:
        raise SearchError("evaluation identity has invalid repetitions")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise SearchError("evaluation identity has invalid warmups")
    _validate_scoring_policy(value.get("scoringPolicy"), "evaluation scoring policy")


def _is_search_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_experiment_contract(experiment: Mapping[str, Any]) -> None:
    strategy = experiment.get("strategy")
    if not isinstance(strategy, dict):
        raise SearchError("search experiment has invalid strategy")
    if strategy.get("name") == "random-search-v0":
        if set(strategy) != {
            "name",
            "seed",
            "samplingPolicy",
            "mutationProbability",
        }:
            raise SearchError("random search experiment has invalid strategy")
        if (
            strategy.get("samplingPolicy") != RANDOM_SAMPLING_POLICY
            or isinstance(strategy.get("seed"), bool)
            or not isinstance(strategy.get("seed"), int)
        ):
            raise SearchError("search experiment has unsupported random strategy")
        _validate_mutation_probability(strategy.get("mutationProbability"))
    elif strategy.get("name") == "evolution-search-v0":
        if set(strategy) != {
            "name",
            "seed",
            "samplingPolicy",
            "populationSize",
            "eliteFraction",
            "initialMutationProbability",
            "mutationProbability",
            "failureCost",
        }:
            raise SearchError("evolution search experiment has invalid strategy")
        if (
            strategy.get("samplingPolicy") != EVOLUTION_SAMPLING_POLICY
            or isinstance(strategy.get("seed"), bool)
            or not isinstance(strategy.get("seed"), int)
        ):
            raise SearchError("search experiment has unsupported evolution strategy")
        _validate_evolution_parameters(
            strategy.get("populationSize"),
            strategy.get("eliteFraction"),
            strategy.get("initialMutationProbability"),
            strategy.get("mutationProbability"),
            strategy.get("failureCost"),
        )
    else:
        raise SearchError("search experiment has unsupported strategy")
    budget = experiment.get("budget")
    if not isinstance(budget, dict) or set(budget) != {
        "maxEvaluations",
        "maxWallTimeSeconds",
        "perTrialTimeoutSeconds",
        "maxConsecutiveDuplicates",
    }:
        raise SearchError("search experiment has invalid budget")
    if (
        isinstance(budget["maxEvaluations"], bool)
        or not isinstance(budget["maxEvaluations"], int)
        or budget["maxEvaluations"] <= 0
    ):
        raise SearchError("search experiment has invalid evaluation budget")
    if (
        isinstance(budget["maxConsecutiveDuplicates"], bool)
        or not isinstance(budget["maxConsecutiveDuplicates"], int)
        or budget["maxConsecutiveDuplicates"] <= 0
    ):
        raise SearchError("search experiment has invalid duplicate budget")
    for field in ("maxWallTimeSeconds", "perTrialTimeoutSeconds"):
        value = budget[field]
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
        ):
            raise SearchError(f"search experiment has invalid {field}")
    evaluator = experiment.get("evaluator")
    evaluator_fields = {
        "version",
        "tsaCliJar",
        "tsaCliJarSha256",
        "java",
        "host",
        "backendRole",
        "repetitions",
        "warmups",
        "progressIntervalSeconds",
        "parameterSpace",
        "parameterSpaceSha256",
        "baseline",
        "baselineSha256",
        "baselineManifest",
        "baselineManifestSha256",
        "workload",
        "scoringPolicy",
    }
    if not isinstance(evaluator, dict) or set(evaluator) != evaluator_fields:
        raise SearchError("search experiment has invalid evaluator contract")
    if evaluator.get("version") != SEARCH_EVALUATOR_VERSION:
        raise SearchError("search experiment has unsupported evaluator version")
    if evaluator.get("backendRole") not in {"regular-bv", "integer-blasted"}:
        raise SearchError("search experiment has invalid backend role")
    for field in (
        "tsaCliJar",
        "java",
        "parameterSpace",
        "baseline",
        "baselineManifest",
    ):
        if not isinstance(evaluator.get(field), str) or not evaluator[field]:
            raise SearchError(f"search experiment has invalid {field}")
    _validate_host_identity(evaluator.get("host"), "search experiment host")
    for field, minimum in (("repetitions", 1), ("warmups", 0), ("progressIntervalSeconds", 0)):
        value = evaluator.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise SearchError(f"search experiment has invalid {field}")
    for field in (
        "tsaCliJarSha256",
        "parameterSpaceSha256",
        "baselineSha256",
        "baselineManifestSha256",
    ):
        value = evaluator.get(field)
        if not _is_search_sha256(value):
            raise SearchError(f"search experiment has invalid {field}")
    _validate_scoring_policy(evaluator.get("scoringPolicy"), "search scoring policy")
    try:
        validate_workload_manifest(evaluator["workload"])
    except EvaluatorError as failure:
        raise SearchError(f"search experiment has invalid workload: {failure}") from failure
    objective = experiment.get("objective")
    if objective != {
        "name": "workload-geometric-normalized-cost-v0",
        "direction": "minimize",
    }:
        raise SearchError("search experiment has invalid objective")


def _log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def _write_trial_failure(
    trial_directory: Path,
    *,
    candidate_id: str,
    status: str,
    failure: BaseException,
    process_timeout_seconds: float | None,
    return_code: int | None = None,
    signal_number: int | None = None,
) -> None:
    atomic_write_json(
        trial_directory / "failure.json",
        {
            "schemaVersion": "cvc5-search-trial-failure-v0",
            "candidateId": candidate_id,
            "status": status,
            "failureType": type(failure).__name__,
            "message": str(failure),
            "atEpochMillis": int(time.time() * 1000),
            "processTimeoutSeconds": process_timeout_seconds,
            "returnCode": return_code,
            "signalNumber": signal_number,
        },
    )


def run_search(arguments: argparse.Namespace) -> dict[str, Any]:
    parameter_space = load_json(arguments.parameter_space)
    _validate_search_parameter_space(parameter_space)
    baseline = load_json(arguments.baseline)
    if not arguments.tsa_cli_jar.is_file():
        raise SearchError(f"TSA CLI jar does not exist: {arguments.tsa_cli_jar}")
    try:
        validate_report(baseline)
        workload = describe_workload(
            profile_directory=arguments.profile_dir,
            profiles=arguments.profile,
            backend_role=arguments.backend_role,
        )
        baseline_manifest = load_json(arguments.baseline_manifest)
        validate_baseline_manifest(
            baseline_manifest,
            report_path=arguments.baseline,
            report=baseline,
            parameter_space_path=arguments.parameter_space,
            parameter_space=parameter_space,
            tsa_cli_jar=arguments.tsa_cli_jar,
            current_workload=workload,
        )
    except EvaluatorError as failure:
        raise SearchError(f"baseline preflight failed: {failure}") from failure
    if arguments.max_evaluations <= 0:
        raise SearchError("max evaluations must be positive")
    if arguments.repetitions <= 0:
        raise SearchError("repetitions must be positive")
    if arguments.warmups < 0:
        raise SearchError("warmups must not be negative")
    if arguments.progress_interval_seconds < 0:
        raise SearchError("progress interval must not be negative")
    if baseline["backendRole"] != arguments.backend_role:
        raise SearchError("baseline and search backend roles do not match")
    if arguments.max_wall_time_seconds is not None and arguments.max_wall_time_seconds <= 0:
        raise SearchError("max wall time must be positive")
    if arguments.per_trial_timeout_seconds is not None and arguments.per_trial_timeout_seconds <= 0:
        raise SearchError("per-trial timeout must be positive")
    if arguments.max_consecutive_duplicates <= 0:
        raise SearchError("max consecutive duplicates must be positive")
    _validate_mutation_probability(arguments.random_mutation_probability)
    evolution_failure_cost = math.exp(arguments.reward_clip) * 10.0
    if arguments.strategy == "evolution":
        _validate_evolution_parameters(
            arguments.population_size,
            arguments.elite_fraction,
            arguments.evolution_initial_mutation_probability,
            arguments.evolution_mutation_probability,
            evolution_failure_cost,
        )

    parameter_space_hash = sha256_json(parameter_space)
    baseline_hash = sha256_json(baseline)
    jar_hash = sha256_file(arguments.tsa_cli_jar)
    scoring_policy = {
        "timeoutPenalty": arguments.timeout_penalty,
        "rewardClip": arguments.reward_clip,
        "excludeRecordedFailures": not arguments.include_recorded_failures,
    }
    host_identity = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "node": platform.node(),
    }
    strategy_contract = (
        {
            "name": "random-search-v0",
            "seed": arguments.seed,
            "samplingPolicy": RANDOM_SAMPLING_POLICY,
            "mutationProbability": arguments.random_mutation_probability,
        }
        if arguments.strategy == "random"
        else {
            "name": "evolution-search-v0",
            "seed": arguments.seed,
            "samplingPolicy": EVOLUTION_SAMPLING_POLICY,
            "populationSize": arguments.population_size,
            "eliteFraction": arguments.elite_fraction,
            "initialMutationProbability": arguments.evolution_initial_mutation_probability,
            "mutationProbability": arguments.evolution_mutation_probability,
            "failureCost": evolution_failure_cost,
        }
    )
    experiment = {
        "strategy": strategy_contract,
        "budget": {
            "maxEvaluations": arguments.max_evaluations,
            "maxWallTimeSeconds": arguments.max_wall_time_seconds,
            "perTrialTimeoutSeconds": arguments.per_trial_timeout_seconds,
            "maxConsecutiveDuplicates": arguments.max_consecutive_duplicates,
        },
        "evaluator": {
            "version": SEARCH_EVALUATOR_VERSION,
            "tsaCliJar": str(arguments.tsa_cli_jar.resolve()),
            "tsaCliJarSha256": jar_hash,
            "java": arguments.java,
            "host": host_identity,
            "backendRole": arguments.backend_role,
            "repetitions": arguments.repetitions,
            "warmups": arguments.warmups,
            "progressIntervalSeconds": arguments.progress_interval_seconds,
            "parameterSpace": str(arguments.parameter_space.resolve()),
            "parameterSpaceSha256": parameter_space_hash,
            "baseline": str(arguments.baseline.resolve()),
            "baselineSha256": baseline_hash,
            "baselineManifest": str(arguments.baseline_manifest.resolve()),
            "baselineManifestSha256": sha256_file(arguments.baseline_manifest),
            "workload": workload,
            "scoringPolicy": scoring_policy,
        },
        "objective": {
            "name": "workload-geometric-normalized-cost-v0",
            "direction": "minimize",
        },
    }

    store = FileSearchStore(arguments.run_dir)
    manifest, state, created = store.open_or_create(experiment)
    strategy: SearchStrategy
    if arguments.strategy == "random":
        strategy = RandomSearchStrategy(
            parameter_space,
            arguments.seed,
            mutation_probability=arguments.random_mutation_probability,
            instance_id=workload["workloadSha256"],
            evaluation_seed=0,
            repetition=0,
            budget=None,
        )
    else:
        strategy = EvolutionSearchStrategy(
            parameter_space,
            arguments.seed,
            population_size=arguments.population_size,
            elite_fraction=arguments.elite_fraction,
            initial_mutation_probability=arguments.evolution_initial_mutation_probability,
            mutation_probability=arguments.evolution_mutation_probability,
            failure_cost=evolution_failure_cost,
            instance_id=workload["workloadSha256"],
            evaluation_seed=0,
            repetition=0,
            budget=None,
        )
    if state["strategyState"] is not None:
        strategy.restore(state["strategyState"])
    else:
        state["strategyState"] = strategy.snapshot()
        store.save_state(state)
    if not created and state["status"] == "FINISHED":
        _log(
            f"Run {manifest['runId']} is already finished: {state['stopReason']}; "
            "use a new run directory for a different budget"
        )
        return state
    state["status"] = "RUNNING"
    state["stopReason"] = None
    store.append_event("EXPERIMENT_CREATED" if created else "EXPERIMENT_RESUMED")
    store.save_state(state)
    _log(
        f"{'Created' if created else 'Resumed'} {manifest['runId']}; "
        f"completed={state['completedTrialCount']}, realEvaluations={state['actualEvaluationCount']}"
    )

    cache = FileEvaluationCache(arguments.cache_dir)
    evaluator = RealCvc5ReplayEvaluator(
        tsa_cli_jar=arguments.tsa_cli_jar,
        parameter_space=parameter_space,
        workload=workload,
        baseline_report=baseline,
        profile_directory=arguments.profile_dir,
        profiles=arguments.profile,
        backend_role=arguments.backend_role,
        repetitions=arguments.repetitions,
        warmups=arguments.warmups,
        java=arguments.java,
        progress_interval_seconds=arguments.progress_interval_seconds,
        stream_child_output=not arguments.quiet_child_output,
        timeout_penalty=arguments.timeout_penalty,
        reward_clip=arguments.reward_clip,
        exclude_recorded_failures=not arguments.include_recorded_failures,
    )

    invocation_started = time.monotonic_ns()
    accounted_invocation_nanos = 0
    consecutive_duplicates = 0

    def active_wall_nanos() -> int:
        return state["activeWallTimeNanos"] + (
            time.monotonic_ns() - invocation_started - accounted_invocation_nanos
        )

    def checkpoint() -> None:
        nonlocal accounted_invocation_nanos
        elapsed = time.monotonic_ns() - invocation_started
        delta = elapsed - accounted_invocation_nanos
        state["activeWallTimeNanos"] += max(delta, 0)
        accounted_invocation_nanos = elapsed
        state["strategyState"] = strategy.snapshot()
        store.save_state(state)

    def wall_budget_remaining() -> float | None:
        if arguments.max_wall_time_seconds is None:
            return None
        return arguments.max_wall_time_seconds - active_wall_nanos() / 1_000_000_000

    recovered_pending = state.get("pendingTrial")
    if recovered_pending is not None and recovered_pending.get("attemptState") == "RUNNING":
        last_heartbeat = recovered_pending.get(
            "attemptLastHeartbeatEpochMillis",
            recovered_pending.get("attemptStartedAtEpochMillis", int(time.time() * 1000)),
        )
        unaccounted_millis = max(int(time.time() * 1000) - last_heartbeat, 0)
        conservative_millis = min(
            unaccounted_millis,
            int(PROCESS_HEARTBEAT_INTERVAL_SECONDS * 1000),
        )
        state["activeWallTimeNanos"] += conservative_millis * 1_000_000
        recovered_pending["attemptState"] = "RECOVERED"
        store.append_event(
            "UNCLEAN_TRIAL_RECOVERED",
            trialId=recovered_pending["trialId"],
            candidateId=recovered_pending["candidate"]["candidateId"],
            chargedUnaccountedNanos=conservative_millis * 1_000_000,
        )
        checkpoint()

    try:
        while True:
            if (
                state["pendingTrial"] is None
                and state["actualEvaluationCount"] >= arguments.max_evaluations
            ):
                state["status"] = "FINISHED"
                state["stopReason"] = "EVALUATION_BUDGET_EXHAUSTED"
                break
            remaining = wall_budget_remaining()
            if state["pendingTrial"] is None and remaining is not None and remaining <= 0:
                state["status"] = "FINISHED"
                state["stopReason"] = "WALL_CLOCK_BUDGET_EXHAUSTED"
                break

            pending = state["pendingTrial"]
            if pending is None:
                request = strategy.ask()
                candidate_id = request.candidate["candidateId"]
                if candidate_id in state["seenCandidateIds"]:
                    strategy.reject(request)
                    state["duplicateCount"] += 1
                    consecutive_duplicates += 1
                    store.append_event(
                        "DUPLICATE_SKIPPED",
                        candidateId=candidate_id,
                        proposalIndex=request.proposal_index,
                    )
                    checkpoint()
                    if consecutive_duplicates >= arguments.max_consecutive_duplicates:
                        state["status"] = "FINISHED"
                        state["stopReason"] = "CANDIDATE_SPACE_EXHAUSTED_OR_DUPLICATE_LIMIT"
                        break
                    continue
                consecutive_duplicates = 0
                evaluation_identity = {
                    "evaluatorVersion": SEARCH_EVALUATOR_VERSION,
                    "searchSeed": arguments.seed,
                    "requestId": request.request_id,
                    "instanceId": request.instance_id,
                    "evaluationSeed": request.seed,
                    "repetition": request.repetition,
                    "budget": request.budget,
                    "host": host_identity,
                    "candidate": request.candidate,
                    "parameterSpaceSha256": parameter_space_hash,
                    "baselineSha256": baseline_hash,
                    "tsaCliJarSha256": jar_hash,
                    "workloadSha256": workload["workloadSha256"],
                    "backendRole": arguments.backend_role,
                    "repetitions": arguments.repetitions,
                    "warmups": arguments.warmups,
                    "scoringPolicy": scoring_policy,
                }
                cache_key = cache.key(evaluation_identity)
                trial_id = f"trial-{state['completedTrialCount']:06d}-{candidate_id}"
                pending = {
                    "trialId": trial_id,
                    "requestId": request.request_id,
                    "proposalIndex": request.proposal_index,
                    "candidate": request.candidate,
                    "instanceId": request.instance_id,
                    "evaluationSeed": request.seed,
                    "repetition": request.repetition,
                    "budget": request.budget,
                    "cacheKey": cache_key,
                    "evaluationIdentity": evaluation_identity,
                    "proposedAtEpochMillis": int(time.time() * 1000),
                }
                state["seenCandidateIds"].append(candidate_id)
                state["pendingTrial"] = pending
                trial_dir = store.trial_directory(trial_id)
                atomic_write_json(trial_dir / "candidate.json", request.candidate)
                store.append_event(
                    "CANDIDATE_PROPOSED",
                    trialId=trial_id,
                    requestId=request.request_id,
                    candidateId=candidate_id,
                    instanceId=request.instance_id,
                    proposalIndex=request.proposal_index,
                    cacheKey=cache_key,
                    options=request.candidate["options"],
                )
                checkpoint()

            trial_dir = store.trial_directory(pending["trialId"])
            candidate = pending["candidate"]
            request = _trial_request_from_pending(pending)
            first_process_attempt = pending.get("attemptState") is None
            cached = cache.get(pending["cacheKey"])
            report_on_disk = trial_dir / "report.json"
            score_on_disk = trial_dir / "score.json"
            if report_on_disk.is_file() and score_on_disk.is_file():
                report = load_json(report_on_disk)
                score = load_json(score_on_disk)
                evaluator.validate_artifacts(candidate, report, score)
                cache.put(
                    pending["cacheKey"],
                    candidate=candidate,
                    report=report,
                    score=score,
                    evaluation_identity=pending["evaluationIdentity"],
                )
                result = _trial_result_from_score(
                    request, report, score, cache_hit=False
                )
                store.append_event(
                    "TRIAL_RECOVERED",
                    trialId=pending["trialId"],
                    candidateId=candidate["candidateId"],
                )
                _log(f"Recovered completed artifacts for {candidate['candidateId']}")
            elif cached is not None:
                report = cached["report"]
                score = cached["score"]
                evaluator.validate_artifacts(candidate, report, score)
                atomic_write_json(trial_dir / "report.json", report)
                atomic_write_json(trial_dir / "score.json", score)
                result = _trial_result_from_score(
                    request, report, score, cache_hit=True
                )
                state["cacheHitCount"] += 1
                store.append_event(
                    "CACHE_HIT",
                    trialId=pending["trialId"],
                    candidateId=candidate["candidateId"],
                    cacheKey=pending["cacheKey"],
                )
                _log(f"Cache hit {candidate['candidateId']}")
            elif (
                (
                    first_process_attempt
                    and state["actualEvaluationCount"] >= arguments.max_evaluations
                )
                or (
                    wall_budget_remaining() is not None
                    and wall_budget_remaining() <= 0
                )
            ):
                message = "incomplete trial was recovered after its search budget expired"
                result = _failed_trial_result(
                    request,
                    status="INCOMPLETE_ATTEMPT",
                    failure_message=message,
                    duration_nanos=0,
                )
                _write_trial_failure(
                    trial_dir,
                    candidate_id=candidate["candidateId"],
                    status=result.status,
                    failure=SearchError(message),
                    process_timeout_seconds=None,
                )
                store.append_event(
                    "TRIAL_RECOVERY_FAILED",
                    trialId=pending["trialId"],
                    candidateId=candidate["candidateId"],
                    reason="BUDGET_EXHAUSTED",
                )
            else:
                real_evaluation_number = _claim_real_evaluation_number(state, pending)
                attempt_started = int(time.time() * 1000)
                pending["attemptState"] = "RUNNING"
                pending["attemptStartedAtEpochMillis"] = attempt_started
                pending["attemptLastHeartbeatEpochMillis"] = attempt_started
                store.append_event(
                    "TRIAL_STARTED",
                    trialId=pending["trialId"],
                    candidateId=candidate["candidateId"],
                    realEvaluationNumber=real_evaluation_number,
                )
                checkpoint()

                def process_heartbeat() -> None:
                    pending["attemptLastHeartbeatEpochMillis"] = int(time.time() * 1000)
                    checkpoint()

                remaining = wall_budget_remaining()
                timeouts = [
                    value
                    for value in (arguments.per_trial_timeout_seconds, remaining)
                    if value is not None
                ]
                process_timeout = max(min(timeouts), 0.001) if timeouts else None
                _log(
                    f"Evaluating {candidate['candidateId']} with real cvc5 "
                    f"({state['actualEvaluationCount']}/{arguments.max_evaluations})"
                )
                evaluation_started_nanos = time.monotonic_ns()
                try:
                    report, score, result = evaluator.evaluate(
                        request,
                        trial_dir / "report.json",
                        process_timeout,
                        process_heartbeat,
                    )
                    atomic_write_json(trial_dir / "score.json", score)
                    cache.put(
                        pending["cacheKey"],
                        candidate=candidate,
                        report=report,
                        score=score,
                        evaluation_identity=pending["evaluationIdentity"],
                    )
                except CandidateDisqualifiedError as failure:
                    result = failure.result
                    _write_trial_failure(
                        trial_dir,
                        candidate_id=candidate["candidateId"],
                        status=result.status,
                        failure=failure,
                        process_timeout_seconds=process_timeout,
                    )
                except ReplayTimeoutError as failure:
                    result = _failed_trial_result(
                        request,
                        status="CANDIDATE_TIMEOUT",
                        duration_nanos=time.monotonic_ns() - evaluation_started_nanos,
                        censored=True,
                        failure_message=str(failure),
                    )
                    _write_trial_failure(
                        trial_dir,
                        candidate_id=candidate["candidateId"],
                        status=result.status,
                        failure=failure,
                        process_timeout_seconds=process_timeout,
                    )
                except ReplayProcessError as failure:
                    status = "NATIVE_CRASH" if failure.signal_number is not None else "JVM_FAILURE"
                    result = _failed_trial_result(
                        request,
                        status=status,
                        duration_nanos=time.monotonic_ns() - evaluation_started_nanos,
                        failure_message=str(failure),
                    )
                    _write_trial_failure(
                        trial_dir,
                        candidate_id=candidate["candidateId"],
                        status=status,
                        failure=failure,
                        process_timeout_seconds=process_timeout,
                        return_code=failure.return_code,
                        signal_number=failure.signal_number,
                    )
                except InvalidReplayReportError as failure:
                    result = _failed_trial_result(
                        request,
                        status="INVALID_REPORT",
                        duration_nanos=time.monotonic_ns() - evaluation_started_nanos,
                        failure_message=str(failure),
                    )
                    _write_trial_failure(
                        trial_dir,
                        candidate_id=candidate["candidateId"],
                        status=result.status,
                        failure=failure,
                        process_timeout_seconds=process_timeout,
                    )
                except OSError as failure:
                    result = _failed_trial_result(
                        request,
                        status="INFRASTRUCTURE_FAILURE",
                        duration_nanos=time.monotonic_ns() - evaluation_started_nanos,
                        failure_message=str(failure),
                    )
                    _write_trial_failure(
                        trial_dir,
                        candidate_id=candidate["candidateId"],
                        status=result.status,
                        failure=failure,
                        process_timeout_seconds=process_timeout,
                    )
                except EvaluatorError as failure:
                    result = _failed_trial_result(
                        request,
                        status="EVALUATOR_FAILURE",
                        duration_nanos=time.monotonic_ns() - evaluation_started_nanos,
                        failure_message=str(failure),
                    )
                    _write_trial_failure(
                        trial_dir,
                        candidate_id=candidate["candidateId"],
                        status=result.status,
                        failure=failure,
                        process_timeout_seconds=process_timeout,
                    )

            strategy.tell(result)
            state["completedTrialCount"] += 1
            state["pendingTrial"] = None
            store.append_event(
                "TRIAL_FINISHED",
                trialId=pending["trialId"],
                result=asdict(result),
            )
            incumbent = state["incumbent"]
            if result.eligible and result.cost is not None and (
                incumbent is None or result.cost < incumbent["cost"]
            ):
                state["incumbent"] = {
                    "candidateId": result.candidate_id,
                    "cost": result.cost,
                    "trialId": pending["trialId"],
                }
                store.append_event("INCUMBENT_CHANGED", **state["incumbent"])
            checkpoint()
            cost_text = "n/a" if result.cost is None else f"{result.cost:.6f}"
            _log(
                f"Finished {result.candidate_id}: status={result.status}, "
                f"cost={cost_text}, cacheHit={result.cache_hit}"
            )
    except (KeyboardInterrupt, ExperimentInterrupted) as interruption:
        state["status"] = "INTERRUPTED"
        reason = (
            interruption.signal_name
            if isinstance(interruption, ExperimentInterrupted)
            else "SIGINT"
        )
        state["stopReason"] = reason
        if state["pendingTrial"] is not None:
            state["pendingTrial"]["attemptState"] = "INTERRUPTED"
            state["pendingTrial"]["attemptLastHeartbeatEpochMillis"] = int(
                time.time() * 1000
            )
        store.append_event(
            "EXPERIMENT_INTERRUPTED", signal=reason, pendingTrial=state["pendingTrial"]
        )
        checkpoint()
        _log("Interrupted; checkpoint saved")
        raise

    store.append_event(
        "EXPERIMENT_FINISHED",
        stopReason=state["stopReason"],
        actualEvaluationCount=state["actualEvaluationCount"],
        completedTrialCount=state["completedTrialCount"],
        incumbent=state["incumbent"],
    )
    checkpoint()
    _log(
        f"Finished: reason={state['stopReason']}, completed={state['completedTrialCount']}, "
        f"realEvaluations={state['actualEvaluationCount']}, cacheHits={state['cacheHitCount']}"
    )
    return state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="run or resume Random Search or evolutionary search with real cvc5"
    )
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
    run.add_argument("--strategy", choices=("random", "evolution"), default="random")
    run.add_argument(
        "--random-mutation-probability",
        type=float,
        default=DEFAULT_RANDOM_MUTATION_PROBABILITY,
        help=(
            "independent probability of changing each option away from its default; "
            f"default: {DEFAULT_RANDOM_MUTATION_PROBABILITY}"
        ),
    )
    run.add_argument(
        "--population-size", type=int, default=DEFAULT_EVOLUTION_POPULATION_SIZE
    )
    run.add_argument(
        "--elite-fraction", type=float, default=DEFAULT_EVOLUTION_ELITE_FRACTION
    )
    run.add_argument(
        "--evolution-initial-mutation-probability",
        type=float,
        default=DEFAULT_EVOLUTION_INITIAL_MUTATION_PROBABILITY,
    )
    run.add_argument(
        "--evolution-mutation-probability",
        type=float,
        default=DEFAULT_EVOLUTION_MUTATION_PROBABILITY,
    )
    run.add_argument("--max-evaluations", type=int, required=True)
    run.add_argument("--max-wall-time-seconds", type=float)
    run.add_argument("--per-trial-timeout-seconds", type=float)
    run.add_argument("--max-consecutive-duplicates", type=int, default=1000)
    run.add_argument("--timeout-penalty", type=float, default=10.0)
    run.add_argument("--reward-clip", type=float, default=5.0)
    run.add_argument("--include-recorded-failures", action="store_true")
    run.add_argument("--progress-interval-seconds", type=int, default=15)
    run.add_argument("--quiet-child-output", action="store_true")
    run.add_argument("--java", default="java")

    status = commands.add_parser("status", help="print the latest run checkpoint")
    status.add_argument("--run-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    previous_handlers: dict[int, Any] = {}

    def interrupt(signum: int, _frame: Any) -> None:
        raise ExperimentInterrupted(signal.Signals(signum).name)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    try:
        if arguments.command == "status":
            state = load_json(arguments.run_dir / "state.json")
            print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        run_search(arguments)
        return 0
    except (KeyboardInterrupt, ExperimentInterrupted):
        return 130
    except (EvaluatorError, SearchError, OSError) as failure:
        print(f"search failed: {failure}", file=sys.stderr)
        return 2
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
