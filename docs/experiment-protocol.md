# Experiment protocol

## Unit and split

The independent unit is a complete workload run, contract, project revision, or
analysis campaign—not an individual solver check. Related runs must remain in
the same development or holdout group.

Use three logical partitions for claim-bearing work:

- development for parameter-space, objective, budget, and algorithm changes;
- validation for choosing among already declared variants;
- sealed test for one final confirmation after every choice is frozen.

A second method of the same contract is not automatically an independent
workload group. The manifest must state the grouping key and why it prevents
leakage.

## Frozen inputs

Before a claim-bearing run, freeze:

- workload manifest and grouped split;
- KSMT profile and feature schema versions;
- cvc5 version and parameter-space ID;
- provider and algorithm identifiers, configuration, seed, and stopping rule;
- target-configuration provenance, when training a model, and correctness oracle;
- host policy, timeout, repetitions, and primary objective;
- configurator seeds, confirmation order policy, confidence procedure, minimum
  useful effect, and the exact claim gate;
- cheap/full fidelity definitions and their qualification criteria, when
  staged evaluation is used.

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

When one application workload is split into replay profiles for isolation, its
primary performance objective is the ratio of summed effective costs, not the
unweighted mean of profile log-ratios. Otherwise a millisecond-scale profile
has the same influence as the profile dominating end-to-end runtime. A
per-profile optimizer such as SMAC must weight each normalized instance cost by
its default effective-cost share, so its instance mean reconstructs the same
whole-workload objective used by Random Search and evolution.

Run each configurator with multiple independent seeds. Compare both final
quality and anytime quality at equal wall-clock. A larger number of cheap or
early-failed trials is not by itself evidence of a better configurator.

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

Measure the default system-noise floor before search. Final confirmation must
use seeded randomized interleaving of matched default/candidate trials rather
than running every default trial first and every candidate trial later. Compute
paired intervals over independent workload groups; individual solver checks do
not increase the independent sample count.

Default/default calibration is a repeated-measurement diagnostic, not a
performance claim. Use an even number of matched pairs with exactly balanced
within-pair role order, and bootstrap the pair measurements so a single
validation group does not produce a degenerate group-level interval. Report the
frozen replay objective, full solver-operation time, and wall time separately.
The calibration gate must be derived from the campaign's minimum useful effect
and must stop search when correctness fails, the interval excludes zero, the
median bias is too large, or the interval is too wide. Final candidate
confirmation continues to bootstrap independent workload groups.

Do not treat a prefix, shard, reduced repetition count, or solver timeout as a
valid SMAC/Hyperband fidelity until its ranking is compared with full replay.
At minimum report rank correlation, top-k recall, and sign-flip rate. If cheap
and full evaluations disagree materially, use the cheap evaluation only as a
diagnostic, not as the optimizer's budget axis.

Replay gain and end-to-end gain are separate claims. Before claiming practical
value, compare several candidates on replay and fresh TSA runs, then evaluate
the total tuned path including capture and search overhead.

The concrete v0 artifacts for this protocol are:

- `cvc5-workload-corpus-v0` for grouped split and baseline bindings;
- `cvc5-configurator-campaign-v0` for common seeds, budgets, objective, and gate;
- `cvc5-confirmation-schedule-v0` for paired randomized interleaving;
- `cvc5-confirmation-observations-v0` for measured matched trials;
- `cvc5-grouped-confirmation-summary-v0` for group-level paired statistics.
- `cvc5-default-noise-calibration-summary-v0` for balanced repeated-measurement
  intervals and the pre-search noise gate;
- `cvc5-campaign-workspace-v0` for the frozen portable design and every
  algorithm/seed run slot;
- `cvc5-finalist-index-v0` for an explicit qualification result for every seed
  (`QUALIFIED` or `NO_FEASIBLE_CANDIDATE`) and
  `cvc5-confirmation-candidate-index-v0` for provenance-preserving handoff of
  only full-development-workload-success incumbents to confirmation;
- `cvc5-multi-seed-anytime-summary-v0` for wall-clock-normalized optimizer
  progress without selecting one lucky seed. Missing feasible incumbents use
  the explicit default fallback and partial SMAC instance costs are not treated
  as full-workload speedups.

The current workspace evaluates the transfer of fixed configurations found on
development workloads. It does not yet measure a complete per-instance
`n% tuning / (100-n)% execution` procedure. That end-to-end claim requires a
separate fresh-TSA experiment after solver-level confirmation succeeds.

## Final evaluation

After all choices are frozen, run the declared algorithms on the sealed
workloads. For a model, train once on development workloads and evaluate once
on the grouped holdout. Report negative results. A changed design requires a
new evaluation, but intermediate run history is not committed to this repo.
