"""SMAC3 ask/tell bridge for real per-profile cvc5 trials.

SMAC never owns solver execution in this project.  It proposes one
``(configuration, instance, seed, budget)`` coordinate, the existing
``RealCvc5ProfileTrialEvaluator`` executes it in an isolated TSA JVM, and this
module reports the typed result back to SMAC.  Consequently Random Search and
SMAC share candidate validation, correctness checks, timing, and failure
semantics.

Persistence intentionally lives in ``ksmt_ml.smac_search`` rather than in this
adapter.  That layer reconciles SMAC run history with external evaluator
artifacts; SMAC's internal files alone do not cover interrupted real solver
trials.
"""

from __future__ import annotations

import importlib.metadata
import math
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from ksmt_ml.configspace_adapter import Cvc5ConfigSpaceAdapter, build_configspace
from ksmt_ml.evaluator import sha256_json
from ksmt_ml.search import (
    DEFAULT_RANDOM_MUTATION_PROBABILITY,
    RandomSearchStrategy,
    TrialRequest,
    TrialResult,
    _trial_request_id,
    _validate_trial_result,
)


SMAC_ADAPTER_VERSION = "cvc5-smac3-ask-tell-v0"
SMAC_REQUIRED_VERSION = "2.4.1"
# Successful costs are clipped to at most exp(5) by the current scoring policy.
# A failed trial must therefore be strictly worse than every eligible trial.
DEFAULT_SMAC_FAILURE_COST = math.exp(5.0) * 10.0
SMAC_TIMEOUT_RESULTS = frozenset({"CANDIDATE_TIMEOUT", "KNOWN_STATUS_TIMEOUT"})


class SmacAdapterError(ValueError):
    """SMAC cannot safely represent the requested experiment or result."""


@dataclass(frozen=True)
class SmacTrialFeedback:
    """Version-independent data used to construct SMAC's ``TrialValue``."""

    cost: float
    time_seconds: float
    status: str
    additional_info: dict[str, Any]


@dataclass(frozen=True)
class _PendingTrial:
    request: TrialRequest
    smac_trial_info: Any


class SmacAskTellStrategy:
    """Translate SMAC3 proposals to the shared trial contract and back.

    This is deliberately a serial adapter.  Durable recovery is supplied by
    the external generation/checkpoint protocol in ``ksmt_ml.smac_search``;
    parallel workers would additionally require durable claiming.
    """

    def __init__(
        self,
        *,
        adapter: Cvc5ConfigSpaceAdapter,
        facade: Any,
        trial_value_type: Any,
        trial_info_type: Any,
        status_type: Any,
        instance_ids: Sequence[str],
        crash_cost: float,
    ):
        instances = _validate_instances(instance_ids)
        _validate_crash_cost(crash_cost)
        self._adapter = adapter
        self._facade = facade
        self._trial_value_type = trial_value_type
        self._trial_info_type = trial_info_type
        self._status_type = status_type
        self._instance_ids = frozenset(instances)
        self._crash_cost = float(crash_cost)
        self._next_proposal_index = 0
        self._pending: dict[str, _PendingTrial] = {}

    @classmethod
    def build(
        cls,
        parameter_space: Mapping[str, Any],
        *,
        instance_ids: Sequence[str],
        seed: int,
        n_trials: int,
        repetitions: int,
        output_directory: Path,
        scenario_name: str = "ksmt-cvc5-smac-v0",
        crash_cost: float = DEFAULT_SMAC_FAILURE_COST,
        additional_initial_configurations: int = 8,
        instance_features: Mapping[str, Sequence[float]] | None = None,
        overwrite: bool = True,
        logging_level: int | Path | bool | None = False,
    ) -> "SmacAskTellStrategy":
        """Create the pinned SMAC3 algorithm-configuration facade.

        ``n_trials`` is SMAC's number of *per-profile* trials, not the number
        of complete candidates.  The intensifier may evaluate one candidate
        on several instance/seed coordinates.  No fidelity budgets are
        declared until a cheaper workload has passed the qualification gate.
        """

        instances = _validate_instances(instance_ids)
        _validate_positive_integer("n_trials", n_trials)
        _validate_positive_integer("repetitions", repetitions)
        if (
            isinstance(seed, bool)
            or not isinstance(seed, int)
            or seed < 0
        ):
            raise SmacAdapterError("SMAC seed must be a non-negative integer")
        if (
            isinstance(additional_initial_configurations, bool)
            or not isinstance(additional_initial_configurations, int)
            or additional_initial_configurations < 0
        ):
            raise SmacAdapterError(
                "additional initial configuration count must be non-negative"
            )
        if not isinstance(scenario_name, str) or not scenario_name:
            raise SmacAdapterError("SMAC scenario name must not be empty")
        _validate_crash_cost(crash_cost)

        api = _load_smac()
        adapter = build_configspace(parameter_space, seed=seed)
        features = _normalize_instance_features(instances, instance_features)
        scenario = api.Scenario(
            adapter.configspace,
            name=scenario_name,
            output_directory=Path(output_directory),
            deterministic=False,
            objectives="cost",
            crash_cost=float(crash_cost),
            n_trials=n_trials,
            use_default_config=True,
            instances=instances,
            instance_features=features,
            min_budget=None,
            max_budget=None,
            seed=seed,
            n_workers=1,
        )
        additional = _sparse_initial_configurations(
            adapter,
            parameter_space,
            seed=seed,
            count=min(additional_initial_configurations, max(0, n_trials - 1)),
        )
        initial_design = api.AlgorithmConfigurationFacade.get_initial_design(
            scenario,
            additional_configs=additional,
        )
        intensifier = api.AlgorithmConfigurationFacade.get_intensifier(
            scenario,
            max_config_calls=len(instances) * repetitions,
        )
        facade = api.AlgorithmConfigurationFacade(
            scenario,
            target_function=None,
            initial_design=initial_design,
            intensifier=intensifier,
            logging_level=logging_level,
            overwrite=overwrite,
        )
        return cls(
            adapter=adapter,
            facade=facade,
            trial_value_type=api.TrialValue,
            trial_info_type=api.TrialInfo,
            status_type=api.StatusType,
            instance_ids=instances,
            crash_cost=crash_cost,
        )

    @property
    def facade(self) -> Any:
        """The pinned SMAC facade, exposed for inspected run history only."""

        return self._facade

    @property
    def pending_request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._pending))

    @property
    def next_proposal_index(self) -> int:
        return self._next_proposal_index

    def restore_proposal_index(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SmacAdapterError("SMAC proposal index must be a non-negative integer")
        if self._pending:
            raise SmacAdapterError("cannot restore proposal index with pending trials")
        self._next_proposal_index = value

    def save_native_state(self) -> None:
        """Ask SMAC to persist its current run history and intensifier state."""

        self._facade.optimizer.save()

    def attach_pending(self, request: TrialRequest) -> None:
        """Reattach one externally checkpointed serial trial after restart."""

        if request.request_id in self._pending:
            raise SmacAdapterError(f"SMAC trial is already pending: {request.request_id}")
        if request.instance_id not in self._instance_ids:
            raise SmacAdapterError(
                f"pending SMAC trial uses an unknown instance: {request.instance_id}"
            )
        if request.budget is not None:
            raise SmacAdapterError("pending SMAC trial uses an unqualified fidelity budget")
        configuration = self._adapter.from_candidate(request.candidate)
        info = self._trial_info_type(
            config=configuration,
            instance=request.instance_id,
            seed=request.seed,
            budget=None,
        )
        running = self._facade.runhistory.get_running_trials()
        if info not in running:
            if running:
                raise SmacAdapterError(
                    "SMAC run history contains another pending trial"
                )
            self._facade.runhistory.add_running_trial(info)
        elif len(running) != 1:
            raise SmacAdapterError("serial SMAC run contains multiple pending trials")
        self._pending[request.request_id] = _PendingTrial(request, info)

    def validate_external_counts(
        self,
        *,
        completed_trials: int,
        has_pending_trial: bool,
    ) -> None:
        runhistory = self._facade.runhistory
        expected_running = 1 if has_pending_trial else 0
        if runhistory.finished != completed_trials:
            raise SmacAdapterError(
                "SMAC finished-trial count disagrees with the external checkpoint"
            )
        if len(runhistory.get_running_trials()) != expected_running:
            raise SmacAdapterError(
                "SMAC running-trial count disagrees with the external checkpoint"
            )
        if runhistory.submitted != completed_trials + expected_running:
            raise SmacAdapterError(
                "SMAC submitted-trial count disagrees with the external checkpoint"
            )

    def incumbent_snapshot(self) -> dict[str, Any] | None:
        configuration = self._facade.intensifier.get_incumbent()
        if configuration is None:
            return None
        candidate = self._adapter.to_candidate(configuration)
        cost = self._facade.runhistory.get_cost(configuration)
        if not math.isfinite(float(cost)):
            raise SmacAdapterError("SMAC incumbent has a non-finite empirical cost")
        return {"candidate": candidate, "cost": float(cost)}

    def ask(self) -> TrialRequest:
        info = self._facade.ask()
        instance_id = getattr(info, "instance", None)
        seed = getattr(info, "seed", None)
        budget = getattr(info, "budget", None)
        if instance_id not in self._instance_ids:
            raise SmacAdapterError(
                f"SMAC proposed an unknown workload instance: {instance_id!r}"
            )
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise SmacAdapterError(f"SMAC proposed an invalid seed: {seed!r}")
        if budget is not None:
            raise SmacAdapterError(
                "SMAC proposed a fidelity budget before any fidelity was qualified"
            )

        candidate = self._adapter.to_candidate(getattr(info, "config"))
        request_id = _trial_request_id(
            candidate["candidateId"], instance_id, seed, 0, None
        )
        if request_id in self._pending:
            raise SmacAdapterError(f"SMAC proposed a duplicate pending trial: {request_id}")
        request = TrialRequest(
            request_id=request_id,
            proposal_index=self._next_proposal_index,
            candidate=candidate,
            instance_id=instance_id,
            seed=seed,
            # A SMAC seed identifies the noisy repetition.  Each isolated JVM
            # still performs exactly one replay repetition internally.
            repetition=0,
            budget=None,
        )
        self._next_proposal_index += 1
        self._pending[request_id] = _PendingTrial(request, info)
        return request

    def tell(self, result: TrialResult) -> SmacTrialFeedback:
        _validate_trial_result(result)
        pending = self._pending.get(result.request_id)
        if pending is None:
            raise SmacAdapterError(
                f"SMAC result has no matching pending request: {result.request_id}"
            )
        _validate_result_matches_request(result, pending.request)
        feedback = trial_result_to_smac_feedback(result, crash_cost=self._crash_cost)
        status = getattr(self._status_type, feedback.status, None)
        if status is None:
            raise SmacAdapterError(
                f"SMAC {SMAC_REQUIRED_VERSION} has no status {feedback.status}"
            )
        value = self._trial_value_type(
            cost=feedback.cost,
            time=feedback.time_seconds,
            status=status,
            additional_info=feedback.additional_info,
        )
        self._facade.tell(pending.smac_trial_info, value)
        del self._pending[result.request_id]
        return feedback


def trial_result_to_smac_feedback(
    result: TrialResult,
    *,
    crash_cost: float = DEFAULT_SMAC_FAILURE_COST,
) -> SmacTrialFeedback:
    """Map strict evaluator outcomes to finite SMAC costs and typed status."""

    _validate_trial_result(result)
    _validate_crash_cost(crash_cost)
    if result.eligible:
        assert result.cost is not None
        cost = float(result.cost)
        status = "SUCCESS"
    elif result.status in SMAC_TIMEOUT_RESULTS:
        cost = float(crash_cost)
        status = "TIMEOUT"
    else:
        cost = float(crash_cost)
        status = "CRASHED"
    return SmacTrialFeedback(
        cost=cost,
        time_seconds=result.duration_nanos / 1_000_000_000.0,
        status=status,
        additional_info={
            "adapterVersion": SMAC_ADAPTER_VERSION,
            "requestId": result.request_id,
            "candidateId": result.candidate_id,
            "instanceId": result.instance_id,
            "seed": result.seed,
            "repetition": result.repetition,
            "budget": result.budget,
            "evaluatorStatus": result.status,
            "correctness": result.correctness,
            "censored": result.censored,
            "cacheHit": result.cache_hit,
            "failureMessage": result.failure_message,
        },
    )


def _load_smac() -> SimpleNamespace:
    try:
        installed = importlib.metadata.version("smac")
        from smac import AlgorithmConfigurationFacade, Scenario
        from smac.runhistory.dataclasses import TrialInfo, TrialValue
        from smac.runhistory.enumerations import StatusType
    except (ImportError, importlib.metadata.PackageNotFoundError) as failure:
        raise SmacAdapterError(
            f"SMAC3 {SMAC_REQUIRED_VERSION} is required; install requirements-search.txt"
        ) from failure
    if installed != SMAC_REQUIRED_VERSION:
        raise SmacAdapterError(
            f"SMAC3 version mismatch: expected {SMAC_REQUIRED_VERSION}, got {installed}"
        )
    return SimpleNamespace(
        AlgorithmConfigurationFacade=AlgorithmConfigurationFacade,
        Scenario=Scenario,
        TrialValue=TrialValue,
        TrialInfo=TrialInfo,
        StatusType=StatusType,
    )


def _sparse_initial_configurations(
    adapter: Cvc5ConfigSpaceAdapter,
    parameter_space: Mapping[str, Any],
    *,
    seed: int,
    count: int,
) -> list[Any]:
    """Create a reproducible default-centred initial design for SMAC.

    This affects only SMAC's bootstrap observations.  Afterwards the surrogate
    and acquisition optimizer can explore the complete ConfigSpace.
    """

    if count == 0:
        return []
    sampler = RandomSearchStrategy(
        parameter_space,
        seed=seed,
        mutation_probability=DEFAULT_RANDOM_MUTATION_PROBABILITY,
    )
    default_hash = sha256_json({})
    seen = {default_hash}
    configurations: list[Any] = []
    for _ in range(max(100, count * 100)):
        candidate = sampler.ask().candidate
        key = sha256_json(candidate["options"])
        if key in seen:
            continue
        seen.add(key)
        configurations.append(adapter.from_candidate(candidate))
        if len(configurations) == count:
            return configurations
    # Tiny or highly constrained test spaces may simply contain fewer unique
    # non-default points than requested.  Returning every point found is safer
    # than rejecting an otherwise valid ConfigSpace.
    return configurations


def _validate_instances(instance_ids: Sequence[str]) -> list[str]:
    if isinstance(instance_ids, (str, bytes)):
        raise SmacAdapterError("SMAC instances must be a sequence of profile ids")
    instances = list(instance_ids)
    if not instances:
        raise SmacAdapterError("SMAC requires at least one workload instance")
    if any(not isinstance(value, str) or not value for value in instances):
        raise SmacAdapterError("SMAC instance ids must be non-empty strings")
    if len(set(instances)) != len(instances):
        raise SmacAdapterError("SMAC instance ids must be unique")
    return instances


def _normalize_instance_features(
    instances: Sequence[str],
    features: Mapping[str, Sequence[float]] | None,
) -> dict[str, list[float]]:
    """Return complete finite features for every SMAC workload instance.

    Until a semantic profile feature projection is frozen, one-hot identity
    features let the Random Forest distinguish development profiles without
    claiming similarity or generalization between them.
    """

    if features is None:
        return {
            instance: [1.0 if row == column else 0.0 for column in range(len(instances))]
            for row, instance in enumerate(instances)
        }
    if not isinstance(features, Mapping) or set(features) != set(instances):
        raise SmacAdapterError(
            "SMAC instance features must contain exactly every workload instance"
        )
    normalized: dict[str, list[float]] = {}
    width: int | None = None
    for instance in instances:
        raw = features[instance]
        if isinstance(raw, (str, bytes)):
            raise SmacAdapterError("SMAC instance features must be numeric vectors")
        values = list(raw)
        if not values:
            raise SmacAdapterError("SMAC instance feature vectors must not be empty")
        if width is None:
            width = len(values)
        elif len(values) != width:
            raise SmacAdapterError("SMAC instance feature vectors must have equal width")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in values
        ):
            raise SmacAdapterError("SMAC instance features must be finite numbers")
        normalized[instance] = [float(value) for value in values]
    return normalized


def _validate_positive_integer(label: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SmacAdapterError(f"{label} must be a positive integer")


def _validate_crash_cost(value: Any) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise SmacAdapterError("SMAC crash cost must be finite and positive")


def _validate_result_matches_request(
    result: TrialResult,
    request: TrialRequest,
) -> None:
    expected = {
        "candidate_id": request.candidate["candidateId"],
        "instance_id": request.instance_id,
        "seed": request.seed,
        "repetition": request.repetition,
        "budget": request.budget,
    }
    for field, value in expected.items():
        if getattr(result, field) != value:
            raise SmacAdapterError(f"SMAC result does not match request field: {field}")
