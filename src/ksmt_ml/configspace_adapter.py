"""Lossless ConfigSpace projection of ``cvc5-parameter-space-v0``.

The JSON parameter-space contract is the only source of truth.  Synthetic
hyperparameters are used only when ConfigSpace cannot express a JSON domain
directly and are removed again when a cvc5 candidate is produced.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from ksmt_ml.evaluator import (
    CANDIDATE_SCHEMA_VERSION,
    active_option_names,
    normalize_candidate,
    sha256_json,
    validate_parameter_space,
)


ZERO_SELECTOR_SUFFIX = "__ksmt_zero_v0"


class ConfigSpaceAdapterError(ValueError):
    """The v0 parameter-space contract cannot be represented losslessly."""


@dataclass(frozen=True)
class Cvc5ConfigSpaceAdapter:
    parameter_space: Mapping[str, Any]
    configspace: Any
    zero_selectors: Mapping[str, str]
    _api: SimpleNamespace

    @classmethod
    def build(
        cls, parameter_space: Mapping[str, Any], *, seed: int = 0
    ) -> "Cvc5ConfigSpaceAdapter":
        validate_parameter_space(parameter_space)
        api = _load_configspace()
        space = api.ConfigurationSpace(
            name=parameter_space["parameterSpaceId"], seed=seed
        )
        domains = parameter_space["options"]
        zero_selectors: dict[str, str] = {}
        hyperparameters: list[Any] = []

        for name, domain in sorted(domains.items()):
            hyperparameters.append(_build_hyperparameter(api, name, domain))
            if domain.get("sampling", "uniform") == "log-uniform-or-zero":
                selector = f"{name}{ZERO_SELECTOR_SUFFIX}"
                if selector in domains:
                    raise ConfigSpaceAdapterError(
                        f"synthetic zero selector collides with cvc5 option: {selector}"
                    )
                zero_selectors[name] = selector
                hyperparameters.append(
                    api.CategoricalHyperparameter(
                        selector,
                        choices=[False, True],
                        default_value=domain["default"] == 0,
                        meta={"ksmtRole": "zero-selector-v0", "cvc5Option": name},
                    )
                )
        space.add(hyperparameters)

        conditions: list[Any] = []
        for name, domain in sorted(domains.items()):
            original = domain.get("activeWhen")
            if name in zero_selectors:
                selector_hp = space[zero_selectors[name]]
                if original is not None:
                    conditions.append(
                        _predicate_condition(api, selector_hp, original, space, domains)
                    )
                positive = api.EqualsCondition(space[name], selector_hp, False)
                if original is not None:
                    positive = _join_condition(
                        api,
                        "all",
                        [positive, _predicate_condition(api, space[name], original, space, domains)],
                    )
                conditions.append(positive)
            elif original is not None:
                conditions.append(
                    _predicate_condition(api, space[name], original, space, domains)
                )
        if conditions:
            space.add(conditions)

        forbiddens: list[Any] = []
        for constraint in parameter_space.get("constraints", []):
            for term in _predicate_dnf(constraint["when"], domains):
                clauses: list[Any] = []
                for name, values in sorted(term.items()):
                    if domains[name].get("sampling") == "log-uniform-or-zero":
                        raise ConfigSpaceAdapterError(
                            "forbidden predicates over log-uniform-or-zero are "
                            f"not losslessly representable: {name}"
                        )
                    hp = space[name]
                    ordered = sorted(values, key=repr)
                    if len(ordered) == 1:
                        clauses.append(api.ForbiddenEqualsClause(hp, ordered[0]))
                    else:
                        clauses.append(api.ForbiddenInClause(hp, ordered))
                if not clauses:
                    raise ConfigSpaceAdapterError(
                        f"constraint forbids the entire space: {constraint['reason']}"
                    )
                forbiddens.append(
                    clauses[0]
                    if len(clauses) == 1
                    else api.ForbiddenAndConjunction(*clauses)
                )
        if forbiddens:
            space.add(forbiddens)

        # This also proves that defaults, conditions, and forbiddens agree.
        space.get_default_configuration().check_valid_configuration()
        return cls(dict(parameter_space), space, zero_selectors, api)

    def to_candidate(
        self, configuration: Mapping[str, Any], *, candidate_id: str | None = None
    ) -> dict[str, Any]:
        values = {name: _plain_scalar(value) for name, value in dict(configuration).items()}
        known = set(self.parameter_space["options"]) | set(self.zero_selectors.values())
        unknown = sorted(set(values).difference(known))
        if unknown:
            raise ConfigSpaceAdapterError(
                "configuration contains unknown hyperparameters: " + ", ".join(unknown)
            )

        logical: dict[str, Any] = {}
        for name, domain in self.parameter_space["options"].items():
            selector = self.zero_selectors.get(name)
            if selector is not None:
                if selector not in values:
                    if name in values:
                        raise ConfigSpaceAdapterError(
                            f"inactive zero selector has an active value: {name}"
                        )
                    continue
                if values[selector] is True:
                    if name in values:
                        raise ConfigSpaceAdapterError(
                            f"zero-valued option must deactivate its positive value: {name}"
                        )
                    logical[name] = 0.0
                elif values[selector] is False:
                    if name not in values:
                        raise ConfigSpaceAdapterError(
                            f"positive option value is absent: {name}"
                        )
                    logical[name] = values[name]
                else:
                    raise ConfigSpaceAdapterError(f"invalid zero selector for {name}")
            elif name in values:
                logical[name] = values[name]

        expected_active = active_option_names(self.parameter_space, logical)
        if set(logical) != expected_active:
            raise ConfigSpaceAdapterError(
                "configuration active options disagree with cvc5 activeWhen semantics"
            )
        patch = {
            name: value
            for name, value in logical.items()
            if value != self.parameter_space["options"][name]["default"]
        }
        identity = {
            "parameterSpaceId": self.parameter_space["parameterSpaceId"],
            "targetSolver": "cvc5",
            "options": patch,
        }
        resolved_id = candidate_id or f"cvc5-configspace-v0-{sha256_json(identity)[:20]}"
        candidate = {
            "schemaVersion": CANDIDATE_SCHEMA_VERSION,
            "candidateId": resolved_id,
            **identity,
        }
        normalize_candidate(candidate, self.parameter_space)
        return candidate

    def from_candidate(self, candidate: Mapping[str, Any]) -> Any:
        raw_options = candidate.get("options")
        if not isinstance(raw_options, Mapping):
            raise ConfigSpaceAdapterError("candidate options must be an object")
        typed_options = {
            name: _typed_value(value, self.parameter_space["options"].get(name), name)
            for name, value in raw_options.items()
        }
        typed_candidate = {**candidate, "options": typed_options}
        normalize_candidate(typed_candidate, self.parameter_space)
        active = active_option_names(self.parameter_space, typed_options)
        effective = {
            name: domain["default"]
            for name, domain in self.parameter_space["options"].items()
        }
        effective.update(typed_options)
        values: dict[str, Any] = {}
        for name in sorted(active):
            value = effective[name]
            selector = self.zero_selectors.get(name)
            if selector is not None:
                is_zero = value == 0
                values[selector] = is_zero
                if not is_zero:
                    values[name] = value
            else:
                values[name] = value
        configuration = self._api.Configuration(self.configspace, values=values)
        configuration.check_valid_configuration()
        return configuration


def build_configspace(
    parameter_space: Mapping[str, Any], *, seed: int = 0
) -> Cvc5ConfigSpaceAdapter:
    return Cvc5ConfigSpaceAdapter.build(parameter_space, seed=seed)


def _load_configspace() -> SimpleNamespace:
    try:
        from ConfigSpace import (
            AndConjunction,
            CategoricalHyperparameter,
            Configuration,
            ConfigurationSpace,
            EqualsCondition,
            ForbiddenAndConjunction,
            ForbiddenEqualsClause,
            ForbiddenInClause,
            InCondition,
            NotEqualsCondition,
            OrdinalHyperparameter,
            OrConjunction,
            UniformFloatHyperparameter,
            UniformIntegerHyperparameter,
        )
    except ImportError as failure:
        raise ConfigSpaceAdapterError(
            "ConfigSpace 1.2.2 is required; install requirements-search.txt"
        ) from failure
    return SimpleNamespace(**locals())


def _build_hyperparameter(api: SimpleNamespace, name: str, domain: Mapping[str, Any]) -> Any:
    kind = domain["kind"]
    meta = {"ksmtRole": "cvc5-option-v0", "sampling": domain.get("sampling", "uniform")}
    if kind == "boolean":
        return api.CategoricalHyperparameter(
            name, choices=[False, True], default_value=domain["default"], meta=meta
        )
    if kind == "categorical":
        return api.CategoricalHyperparameter(
            name, choices=domain["values"], default_value=domain["default"], meta=meta
        )
    if kind == "integer":
        step = domain.get("step", 1)
        if step != 1:
            if domain.get("sampling", "uniform") != "uniform":
                raise ConfigSpaceAdapterError(
                    f"stepped log integer is not losslessly representable: {name}"
                )
            sequence = list(range(domain["minimum"], domain["maximum"] + 1, step))
            if domain["default"] not in sequence:
                raise ConfigSpaceAdapterError(f"integer default is off-grid: {name}")
            return api.OrdinalHyperparameter(
                name, sequence=sequence, default_value=domain["default"], meta=meta
            )
        return api.UniformIntegerHyperparameter(
            name,
            lower=domain["minimum"],
            upper=domain["maximum"],
            default_value=domain["default"],
            log=domain.get("sampling", "uniform") == "log-uniform",
            meta=meta,
        )
    if kind == "number":
        sampling = domain.get("sampling", "uniform")
        lower = domain["minimum"]
        default = domain["default"]
        if sampling == "log-uniform-or-zero":
            lower = domain["positiveMinimum"]
            default = lower if default == 0 else default
        return api.UniformFloatHyperparameter(
            name,
            lower=lower,
            upper=domain["maximum"],
            default_value=default,
            log=sampling in {"log-uniform", "log-uniform-or-zero"},
            meta=meta,
        )
    raise ConfigSpaceAdapterError(f"unsupported cvc5 domain kind: {kind}")


def _predicate_condition(
    api: SimpleNamespace,
    child: Any,
    predicate: Mapping[str, Any],
    space: Any,
    domains: Mapping[str, Mapping[str, Any]],
    *,
    negate: bool = False,
) -> Any:
    if "option" in predicate:
        parent_name = predicate["option"]
        if domains[parent_name].get("sampling") == "log-uniform-or-zero":
            raise ConfigSpaceAdapterError(
                f"predicates over log-uniform-or-zero are unsupported: {parent_name}"
            )
        parent = space[parent_name]
        if "equals" in predicate:
            condition = api.NotEqualsCondition if negate else api.EqualsCondition
            return condition(child, parent, predicate["equals"])
        values = list(predicate["in"])
        if not negate:
            return api.InCondition(child, parent, values)
        return _join_condition(
            api,
            "all",
            [api.NotEqualsCondition(child, parent, value) for value in values],
        )
    if "not" in predicate:
        return _predicate_condition(
            api, child, predicate["not"], space, domains, negate=not negate
        )
    operator = "all" if "all" in predicate else "any"
    if negate:
        operator = "any" if operator == "all" else "all"
    children = predicate["all"] if "all" in predicate else predicate["any"]
    return _join_condition(
        api,
        operator,
        [
            _predicate_condition(api, child, item, space, domains, negate=negate)
            for item in children
        ],
    )


def _join_condition(api: SimpleNamespace, operator: str, conditions: Sequence[Any]) -> Any:
    if len(conditions) == 1:
        return conditions[0]
    conjunction = api.AndConjunction if operator == "all" else api.OrConjunction
    return conjunction(*conditions)


def _predicate_dnf(
    predicate: Mapping[str, Any],
    domains: Mapping[str, Mapping[str, Any]],
    *,
    negate: bool = False,
) -> list[dict[str, frozenset[Any]]]:
    if "option" in predicate:
        name = predicate["option"]
        values = {predicate["equals"]} if "equals" in predicate else set(predicate["in"])
        if negate:
            finite = _finite_values(name, domains[name])
            if finite is None:
                raise ConfigSpaceAdapterError(
                    f"negated numeric forbidden predicate is not finite: {name}"
                )
            values = set(finite).difference(values)
        return [] if not values else [{name: frozenset(values)}]
    if "not" in predicate:
        return _predicate_dnf(predicate["not"], domains, negate=not negate)
    source_operator = "all" if "all" in predicate else "any"
    operator = source_operator
    if negate:
        operator = "any" if source_operator == "all" else "all"
    children = predicate[source_operator]
    terms = [_predicate_dnf(child, domains, negate=negate) for child in children]
    if operator == "any":
        return [term for group in terms for term in group]
    combined: list[dict[str, frozenset[Any]]] = [{}]
    for group in terms:
        next_terms: list[dict[str, frozenset[Any]]] = []
        for left, right in itertools.product(combined, group):
            merged = dict(left)
            valid = True
            for name, values in right.items():
                intersection = values if name not in merged else merged[name].intersection(values)
                if not intersection:
                    valid = False
                    break
                merged[name] = frozenset(intersection)
            if valid:
                next_terms.append(merged)
        combined = next_terms
    return combined


def _finite_values(name: str, domain: Mapping[str, Any]) -> list[Any] | None:
    if domain["kind"] == "boolean":
        return [False, True]
    if domain["kind"] == "categorical":
        return list(domain["values"])
    if domain["kind"] == "integer":
        return list(
            range(domain["minimum"], domain["maximum"] + 1, domain.get("step", 1))
        )
    return None


def _typed_value(value: Any, domain: Mapping[str, Any] | None, name: str) -> Any:
    if domain is None:
        raise ConfigSpaceAdapterError(f"candidate contains unknown cvc5 option: {name}")
    typed = value
    kind = domain["kind"]
    if isinstance(value, str):
        try:
            if kind == "boolean" and value in {"true", "false"}:
                typed = value == "true"
            elif kind == "integer":
                typed = int(value)
            elif kind == "number":
                typed = float(value)
        except ValueError as failure:
            raise ConfigSpaceAdapterError(
                f"invalid serialized value for {name}: {value}"
            ) from failure
    if (
        domain.get("sampling") == "log-uniform-or-zero"
        and typed != 0
        and typed < domain["positiveMinimum"]
    ):
        raise ConfigSpaceAdapterError(
            f"{name} must be zero or at least {domain['positiveMinimum']}"
        )
    return typed


def _plain_scalar(value: Any) -> Any:
    item = getattr(value, "item", None)
    return item() if callable(item) else value
