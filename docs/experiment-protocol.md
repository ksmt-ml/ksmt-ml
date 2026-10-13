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
- target-configuration provenance and correctness oracle;
- training algorithm, hyperparameters, seed, and stopping rule;
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

## Required baselines

- default cvc5 configuration;
- best fixed configuration selected only on development workloads;
- simple deterministic profile heuristic;
- learned predictor.

The predictor is compared with the same correctness oracle and workload split.
Its inference cost is reported separately from profiling and solver execution.

## Metrics and gate

Always report correctness failures, statuses, total workload time, checks per
second or another frozen objective, p50/p95/p99 where meaningful, profiling and
inference overhead, and workload-macro aggregates.

Model work requires zero wrong answers among retained configurations, stable
headroom over default and best fixed, and consistent improvement across
independent workload groups. A failed gate changes the workload coverage,
parameter space, or objective; it is not repaired by silently fitting a larger
model.

## Final evaluation

After all choices are frozen, train once on development workloads and evaluate
once on the sealed holdout. Report negative results. A changed design requires a
new evaluation, but intermediate run history is not committed to this repo.
