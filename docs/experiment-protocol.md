# Experiment protocol

## Unit and split

The independent unit is a complete workload run, contract, project revision, or
analysis campaign—not an individual solver check. Related runs must remain in
the same development or holdout group.

## Frozen inputs

Before a claim-bearing run, freeze:

- workload manifest and grouped split;
- KSMT profile and feature schema versions;
- cvc5 version and parameter-space ID;
- provider and algorithm identifiers, configuration, seed, and stopping rule;
- target-configuration provenance, when training a model, and correctness oracle;
- host policy, timeout, repetitions, and primary objective.

Git stores specifications and final aggregate evidence, not tuning run history.
Raw run shards remain in ignored or approved external storage.

## Label and benchmark contract

Every labelled target must record its origin and the cvc5/profile/feature schema
versions it belongs to. Every benchmarked configuration receives the same
workload policy on a fresh solver. Apply the patch before assertions. Record the
exact patch, result statuses, objective, wall time, timeout, crash, invalid
option, wrong answer, host, seed, repetition, and code revisions. Preserve
failures explicitly.

If workload replay is not equivalent, document the drift and do not infer
causal gains from unrelated before/after runs.

## Required search baselines

- default cvc5 configuration;
- deterministic Random Search;
- evolutionary search;
- SMAC.

All algorithms receive the same host-enforced budget, workload policy,
correctness oracle, and aggregation rule. Report worker and host overhead
separately from solver execution.

For model evaluation, also include the best fixed configuration selected only
on development workloads and a simple deterministic profile heuristic. Compare
the predictor on the same grouped split, and report inference cost separately.

## Metrics and gate

Always report correctness failures, statuses, total workload time, checks per
second or another frozen objective, p50/p95/p99 where meaningful, profiling,
search-host, worker, and inference overhead where applicable, plus
workload-macro aggregates.

Every retained configuration requires zero wrong answers. Any performance claim
requires stable headroom over default and the declared baselines and consistent
improvement across independent workload groups. A failed gate changes the
workload coverage, parameter space, objective, or provider; it is not repaired
by silently changing the search or model after inspecting the holdout.

## Final evaluation

After all choices are frozen, run the declared algorithms on the sealed
workloads. For a model, train once on development workloads and evaluate once
on the grouped holdout. Report negative results. A changed design requires a
new evaluation, but intermediate run history is not committed to this repo.
