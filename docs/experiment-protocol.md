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

Baselines answer different questions and are reported in separate groups.

### Configuration baselines

- default cvc5 configuration;
- best fixed configuration selected only on development workloads;
- oracle best observed configuration per workload, marked as a non-deployable
  post-hoc upper reference.

The best-fixed configuration does not restrict the search space and is not the
target architecture. It tests whether workload-specific adaptation adds value.

### Search-procedure baselines

- Random Search;
- categorical ES or Cross-Entropy Method;
- SMAC3 without multi-fidelity;
- SMAC3 with development-qualified multi-fidelity.

Search procedures receive the same parameter space, constraints, evaluator,
trial-status policy, correctness gate, wall-clock budget, and finalist
confirmation policy. Report evaluation count as a sample-efficiency diagnostic,
not as a substitute for equal wall-clock cost.

Compare complete configurator runs over multiple configurator seeds. Report
incumbent cost against wall-clock and evaluation count, area under the anytime
curve, final incumbent quality, and the probability of finding the declared
minimum improvement.

### Learned-tuner baselines

- default cvc5;
- development-only best fixed configuration;
- simple deterministic profile heuristic;
- learned custom-parameter predictor;
- development-only SMAC result as expensive oracle headroom, not as a runtime
  competitor.

The predictor is compared with the same correctness oracle and workload split.
Its inference cost is reported separately from profiling and solver execution.

### End-to-end budget baseline

For a strict total budget `B`, the default system receives `B` for the full TSA
run. The tuned system must fit profiling, configuration search or inference,
and the fresh final TSA run into the same `B`. For amortized evaluation, freeze
the reuse horizon and report it separately from the strict single-run result.

## Metrics and gate

Always report correctness failures, statuses, total workload time, checks per
second or another frozen objective, p50/p95/p99 where meaningful, profiling and
inference overhead, and workload-macro aggregates.

Model work requires zero wrong answers among retained configurations, stable
headroom over default and best fixed, and consistent improvement across
independent workload groups. A failed gate changes the workload coverage,
parameter space, or objective; it is not repaired by silently fitting a larger
model.

## Offline replay objective v0

Before candidate search, replay the frozen profiles with the default cvc5
configuration and require every reference `SAT`/`UNSAT` result to be reproduced.
Reject recorded workload failures rather than silently including or deleting
them. Publish the report together with a `cvc5-baseline-manifest-v0` that binds
its SHA-256 to the exact profile bytes, parameter space, TSA runner, default
candidate, backend role, and compatible cvc5 version. Search must repeat this
preflight before creating or resuming a campaign. Cache the qualified report as
the baseline for the campaign. In v0 both baseline and candidate replay use
`per-profile-process-v0`: every `(profile, repetition)` coordinate runs in a
fresh TSA JVM, and the workload report is assembled only after the complete
trial matrix is available. Baselines produced by the previous whole-workload
process mode are not comparable and are rejected by the manifest contract.

For each profile and candidate, use the median measured cost across repetitions.
The v0 cost contains non-check solver operations and checks whose reference
status is known. Replace the measured duration of a candidate timeout with the
declared timeout multiplied by the frozen penalty factor. Do not include checks
whose reference status is `UNKNOWN` in the objective until an independent
correctness oracle is available.

Compute `log(default_cost / candidate_cost)` per independent workload and take
the macro mean after clipping. A contradictory `SAT`/`UNSAT` answer, invalid
option, crash, or incomplete replay makes the candidate ineligible. Exclude
profiles containing recorded workload failures by default and report every
exclusion explicitly.

The human-facing reward is not necessarily the configurator target. A
minimizing configurator such as SMAC3 should receive a normalized cost such as
`candidate_effective_cost / default_effective_cost` or its logarithm, plus an
explicit timeout/crash status. Preserve measured duration, cutoff, censoring
status, and the PAR-style adjusted value as separate fields.

## Fidelity qualification

Do not assume that a temporal profile prefix is a valid cheap fidelity. Before
using Successive Halving, Hyperband, or aggressive promotion, evaluate a frozen
candidate sample at every proposed fidelity and report rank correlation, top-k
recall, speedup sign changes, and timeout/correctness drift.

A fidelity is enabled only after meeting thresholds frozen in the experiment
manifest. If temporal prefixes fail, use a justified alternative such as
representative shards, stratified checks, or fewer complete instances.

## Replay-to-application transfer

Replay improvement is a solver-level result. Before making an application-level
claim, evaluate a representative range of candidate configurations both by
replay and by fresh TSA execution. Report rank correlation, top-k recall, false
replay winners, correctness drift, coverage drift, and total system cost.

If replay does not preserve useful end-to-end ranking, revise the objective or
include end-to-end trials in configuration search.

## Final evaluation

After all choices are frozen, train once on development workloads and evaluate
once on the sealed holdout. Report negative results. A changed design requires a
new evaluation, but intermediate run history is not committed to this repo.
