# Project roadmap

This is the authoritative implementation and experiment roadmap for `ksmt-ml`.
The rationale, literature mapping, and plain-language design are documented in
[`docs/per-instance-tuning-plan-ru.md`](docs/per-instance-tuning-plan-ru.md).

## Objective

Deliver one budgeted per-workload tuning system without conflating optimizer
training with a separately deployed predictive model.

### MVP-A — prove that the workload is tunable

Given a TSA workload profile and a fixed budget, search the cvc5 parameter
space with real solver replay, select a newly constructed configuration, and
verify it in a fresh end-to-end TSA run.

MVP-A compares configuration procedures, not just isolated configurations:

- Random Search;
- categorical ES or Cross-Entropy Method;
- SMAC3;
- default cvc5 as the no-tuning control.

SMAC's Random Forest is an internal, temporary surrogate used to choose the
next real cvc5 trial. It is not a second production model and is not applied on
top of the chosen configuration. The deployed result of one tuning run is a
validated cvc5 candidate JSON.

The primary end-to-end claim is:

```text
profile collection + bounded tuning + fresh tuned TSA run
    < fresh default TSA run
```

under the same total budget and without correctness or coverage regression.

## Canonical implementation sequence (steps 1–9)

This is the active execution order. Work advances one numbered step per
operator hand-off; expensive JVM/replay experiments are run by the operator.
All contracts remain `v0` until the first merge.

1. **Trial-level `ask/tell` contract.** Represent one request by candidate,
   workload instance, seed, repetition and optional fidelity budget; represent
   its result by minimized cost, duration, censoring, correctness and status.
   Adapt Random Search and persisted state to this contract.
2. **Per-profile evaluator.** Evaluate one `(candidate, profile, repetition)`
   trial with real cvc5 and aggregate trial results separately from the
   optimizer-facing primitive.
3. **ConfigSpace adapter.** Translate the reviewed v0 cvc5 parameter space,
   conditions and forbidden combinations without maintaining a second manual
   parameter catalogue.
4. **Short Random Search smoke.** Run 5–10 real candidates to validate the
   complete trial, cache, resume and aggregation path; Random Search remains
   the scientific control, not the main expensive experiment.
5. **SMAC3 adapter.** Connect the same trial evaluator to SMAC, using workload
   profiles as instances, repetitions as seeds and only qualified fidelities as
   budgets.
6. **SMAC persistence and resume.** Preserve run history, scenario, incumbent
   and external budget accounting so interruption does not invalidate a run.
7. **Equal-budget comparison.** Compare Random Search and SMAC using identical
   wall-clock/evaluation limits, parameter space, correctness gate and workload.
8. **Fresh full TSA application.** Apply the selected custom candidate before
   assertions to a fresh solver and execute the full TSA task, recording
   correctness, coverage and total time.
9. **Final budget-split experiment.** Freeze and test the selected split (for
   example 10% profiling/tuning and 90% final execution) against an untuned
   100% run; include all tuning overhead in the tuned arm.

Current status: steps 1–4 are implemented locally. The five-candidate Random
Search smoke completed with four clean candidates and one correctly censored
known-status timeout. Steps 5–9 are pending; the consolidated repository-wide
verification remains planned after step 9.

## End-to-end path

```text
current foundation
    -> trial-level ask/tell
    -> per-profile real-cvc5 trials
    -> ConfigSpace adapter
    -> short Random Search control
    -> resumable SMAC3
    -> equal-budget optimizer comparison
    -> fresh full TSA application
    -> final budget-split experiment
```

## Evidence invariants

Every claim-bearing experiment follows these rules:

1. A candidate is evaluated by a real cvc5 backend, not by recorded timings.
2. Wrong SAT/UNSAT answers, invalid options, crashes, and incomplete replay are
   preserved and cannot silently become ordinary slow observations.
3. Configuration procedures receive the same wall-clock budget, workload
   policy, and correctness gate.
4. Search, model selection, and budget selection use development workloads
   only. Related runs remain in the same workload group.
5. Replay improvement is reported as replay improvement until it is confirmed
   by a fresh end-to-end TSA run.
6. Profiling, search, inference, and final execution costs are reported
   separately and together.
7. Raw run history is retained outside Git; versioned specifications and final
   aggregate evidence are reviewable in Git.

## Baseline matrix

Different questions require different baselines. They must not be collapsed
into one list.

### Configuration baselines

| Baseline | Purpose |
|---|---|
| default cvc5 | No-tuning control and cost normalizer |
| best fixed on development workloads | Tests whether per-workload adaptation is needed |
| oracle best observed per workload | Post-hoc upper reference; never a deployable baseline |

The best-fixed baseline is selected without holdout data. It is not the target
architecture and does not restrict the search space.

### Search-procedure baselines

| Procedure | Purpose |
|---|---|
| Random Search | Minimum credible black-box search control |
| categorical ES/CEM | Tests the evolutionary-search hypothesis |
| SMAC3 without multi-fidelity | Tests the model-based search contribution |
| SMAC3 with qualified multi-fidelity | Tests the value of staged evaluation |

All procedures use the same parameter constraints, evaluator, trial-status
policy, wall-clock limit, and final confirmation protocol. Evaluation count is
reported in addition to wall-clock, but it is not the primary fairness budget.

### End-to-end system baselines

Under a total budget `B`, compare:

```text
default system:  full TSA run <= B
tuned system:    profile + bounded search + fresh TSA run <= B
```

For amortized use, report the declared number of future runs and compare total
cost over exactly that horizon. Do not mix strict single-run and amortized
claims.

## Milestone 0 — preserve the working foundation

The local implementation currently provides:

- KSMT observation-only profile contracts and recording profiler;
- TSA cvc5 profiling with replayable AST;
- a JVM replay evaluator using a fresh `KCvc5Solver`;
- Python process isolation, candidate validation, scoring, and v0 schemas;
- a default candidate and real-profile smoke coverage.

Before extending it, preserve a reproducible default full-profile baseline and
record the exact TSA, KSMT, cvc5, Java, host, profile, and code provenance.

Exit criterion: the default configuration replays every retained known
SAT/UNSAT result with zero mismatches and produces a schema-valid report.

## Milestone 1 — freeze experiment contract v0

Define before running configurators:

- the independent workload unit;
- development, validation, and sealed test groups;
- strict single-run or amortized deployment scenario;
- primary end-to-end objective and secondary diagnostics;
- correctness oracle at replay and TSA levels;
- timeout and censoring policy;
- repetition and host policy;
- configurator wall-clock and evaluation limits;
- candidate promotion and final confirmation rules.

Evaluate tuning fractions such as `1/99`, `3/97`, `5/95`, `10/90`, and `20/80`
on development groups only. Freeze the chosen fraction before sealed testing.

Exit criterion: a versioned `experiment-manifest-v0` can determine whether any
run is comparable without relying on undocumented operator knowledge.

## Milestone 2 — define cvc5 parameter space v0

Current local result: TSA exports all option metadata from the loaded cvc5
1.3.4 runtime, the reviewed inventory classifies all 518 canonical options,
and the regular-bv v0 view exposes 57 active parameters. Conditions and
forbidden combinations use typed predicates; numeric domains and sampling are
explicit. Real replay discovered ten native-crashing options, which remain in
the inventory as `unsafe` and are excluded from the active view. A one-profile
qualification passed 73 singleton/boundary and 20 combined random candidates.

Remaining evidence gate: repeat the qualification over the frozen
multi-profile smoke corpus before treating the view as corpus-qualified.

Maintain a complete version-specific catalogue of cvc5 options, then derive
the active search space from workload theories, lifecycle requirements, and
real-solver smoke results. Do not discard an option merely because cvc5 marks
it `expert`. For every parameter record:

- type and allowed domain;
- default value;
- conditional activation;
- cross-parameter constraints;
- lifecycle restrictions;
- whether the evaluator owns it;
- source and solver-version provenance.

Constraint and conditional-activation evaluation is implemented for v0.
Generate boundary and representative candidates and validate them against the
actual loaded cvc5 runtime.

The initial inventory and proposed cluster policy are recorded in
[`docs/cvc5-1.3.4-option-space-review-ru.md`](docs/cvc5-1.3.4-option-space-review-ru.md).
The catalogue contains all 518 canonical options exposed by cvc5 1.3.4; a
particular experiment activates only the clusters that can affect its workload
and preserves an explicit exclusion reason for every other option.

Exit criterion: Random Search can generate only schema-valid, constraint-valid
candidates, and every domain value has an automated real-solver smoke test.

## Milestone 3 — harden evaluators

### Replay evaluator

- record the version of the cvc5 runtime actually loaded by KSMT;
- distinguish timeout, censored run, crash, invalid option, cancellation, and
  process-budget exhaustion;
- retain measured runtime, cutoff, and PAR-adjusted cost separately;
- support safe cancellation and atomic partial experiment records;
- isolate finalist repetitions strongly enough to control JVM/JIT leakage.

### End-to-end TSA evaluator

- launch TSA in a fresh process with a candidate applied before assertions;
- record wall time, resource limits, exit status, coverage, explored states,
  checks, errors, and result artifacts;
- enforce the TSA-level correctness oracle;
- support default/candidate paired repetitions and randomized run order.

Exit criterion: one candidate can be evaluated by replay and by a fresh TSA run
with linked provenance and explicit correctness results.

## Milestone 4 — implement search orchestration

Current local foundation: the serial v0 loop now provides a strategy-neutral
`ask/tell` boundary, deterministic Random Search, content-addressed file cache,
deduplication, append-only JSONL trace, atomic checkpoint/resume, active
wall-clock and real-evaluation budgets, explicit failures, and incumbent
tracking. It invokes the real TSA/cvc5 replay evaluator. A strict baseline
manifest binds the report to the exact profile bytes, parameter space, TSA JAR,
default candidate, backend, and cvc5 version before a run directory is created.
Persisted reports, checkpoints, events, and cached artifacts are validated on
read; typed outcomes distinguish candidate timeout, native crash, JVM failure,
invalid report, infrastructure failure, and replay correctness failure. SIGINT
and SIGTERM terminate the isolated process group and checkpoint the attempt;
durable process heartbeats bound unaccounted time after an unclean shutdown.
SQLite and synthetic objectives are intentionally absent.

Still required before this milestone is complete: parallel-worker-safe claiming
for multi-host execution, learning-curve aggregation, and the measured
interrupted-run/reproducibility campaign on the frozen workload. Serial
single-host cancellation and recovery are implemented and covered by focused
tests.

Create one strategy-neutral `ask -> evaluate -> tell` loop with:

- deterministic candidate IDs and deduplication;
- cached results keyed by candidate, workload, fidelity, seed, and evaluator
  version;
- wall-clock, trial, per-trial, and cancellation budgets;
- checkpoint/resume;
- append-only trial history including failures;
- parallel-worker-safe claiming;
- incumbent tracking;
- reproducible strategy seeds;
- learning-curve and anytime-metric output.

Implement Random Search first. It validates the complete orchestration without
depending on a surrogate model.

Exit criterion: an interrupted Random Search resumes without repeating finished
trials and produces the same logical candidate sequence for the same seed.

## Milestone 5 — qualify fidelity levels

Produce valid short, medium, and full replay workloads. A temporal prefix must
end at a safe solver boundary; truncating profile JSON is not acceptable.

Evaluate a frozen sample of candidates at every proposed fidelity and measure:

- Spearman and Kendall rank correlation;
- top-k recall from cheap to full fidelity;
- sign changes in speedup;
- timeout and correctness drift;
- stability across workload groups.

If temporal prefixes are not predictive, test stratified check samples,
representative shards, or fewer complete workload instances. Do not enable
Successive Halving solely because the software supports it.

Exit criterion: each enabled cheap fidelity meets a predeclared correlation and
top-k-retention threshold on development workloads.

## Milestone 6 — integrate SMAC3

Map `cvc5-parameter-space-v0` to ConfigSpace and adapt the evaluator to a SMAC3
target function.

The adapter returns a minimized normalized cost, for example:

```text
log(candidate_effective_cost / default_effective_cost)
```

It also preserves timeout, crash, memory-out, and cancellation as trial status.
Use instances for workloads, seeds for noisy repetitions, and budgets only for
fidelities qualified in Milestone 5.

Implement two explicit scenarios:

1. per-instance SMAC for one workload;
2. development-distribution configuration over multiple workload instances.

Exit criterion: SMAC3 can resume from run history, respects the external budget,
and returns a schema-valid incumbent not present in a preset catalog.

## Milestone 7 — implement evolutionary baseline

Implement categorical ES or Cross-Entropy Method over the same constrained
space. Do not apply Gaussian mutation directly to arbitrary encodings of
categorical options.

Freeze population size, update rule, stopping rule, and seeds without consulting
sealed results.

Exit criterion: ES/CEM, Random Search, and SMAC3 can run through the same
orchestrator with identical evaluator and budget policy.

## Milestone 8 — development tunability pilot

Run several independent configurator seeds on one or more long, stable
development workloads. Compare:

- final incumbent cost;
- incumbent cost versus wall-clock;
- incumbent cost versus evaluation count;
- area under the anytime curve;
- probability of finding a declared minimum improvement;
- timeout, crash, invalid-option, and wrong-answer counts.

Re-evaluate finalists using paired, randomized repetitions. Then run the best
confirmed configurations through the end-to-end TSA evaluator.

Exit criterion: either demonstrate repeatable per-instance headroom over
default cvc5 or record a negative result and revise the parameter space or
workload hypothesis before model work.

## Milestone 9 — validate replay-to-TSA transfer

Evaluate a representative range of slow, neutral, and fast replay candidates
end to end. Measure:

- replay/full-TSA rank correlation;
- replay top-k recall for end-to-end winners;
- false replay winners;
- correctness and coverage drift;
- total cost including profiling and search.

If replay ranking does not transfer, revise the replay objective or include
end-to-end trials in the search loop. Do not use one successful finalist as the
only evidence of transfer.

Exit criterion: the replay objective meets a predeclared transfer threshold or
is explicitly demoted to a diagnostic metric.

## Optional research track — learned inference-only tuner

The sections below are not part of active steps 1–9. They are retained only as
a possible later experiment if repeated per-workload search proves useful but
too expensive. They must not be described as an additional model required by
the current system.

### Collect workload corpus and freeze features

Collect independent contracts, methods, revisions, and seeds with explicit
group identity. Preserve all configuration trials, not only incumbents:

```text
profile provenance
feature vector
candidate configuration
fidelity and budget
measured and adjusted cost
solver and TSA result
timeout/crash/censor status
```

Freeze a deterministic profile feature schema shared by Python training and
the JVM runtime. Add cross-runtime golden vectors.

Exit criterion: the corpus supports a group-disjoint train/validation/test split
and every example is traceable to raw profile and evaluation provenance.

### Train inference-only baselines

Train simple models before increasing complexity. The primary model predicts
custom parameter values, not a preset ID. Alternative cost/ranking models may
be studied offline, but runtime search is not silently introduced into the
inference-only architecture.

Compare:

- default cvc5;
- development-only best fixed configuration;
- deterministic profile heuristic;
- learned custom-parameter predictor;
- expensive development-only SMAC oracle headroom.

Validate decoding, parameter constraints, fallback, and prediction stability.

Exit criterion: at least one model emits valid configurations and justifies a
sealed evaluation by outperforming non-ML development baselines without
correctness regression.

### Sealed grouped evaluation

Freeze code, bundle candidate, features, parameter space, budget policy,
baselines, host policy, and statistical analysis before opening test groups.

Report:

- correctness failures and exclusions;
- profiling, search/inference, solver, and total end-to-end time;
- workload-macro speedup distribution;
- p50/p95/p99 where meaningful;
- paired confidence intervals;
- per-group results and negative cases;
- strict-budget and amortized results as separate claims.

Exit criterion: the learned system passes the predefined evidence gate. A
failed gate is reported and does not trigger tuning on sealed test workloads.

### ONNX handoff

Export the accepted predictor with:

```text
model.onnx
manifest.json
feature-schema.json
parameter-space.json
test-vectors.json
sha256sums.txt
README.md
```

The manifest binds profile, feature, parameter-space, cvc5, tensor-contract,
model, and bundle versions. Verify Python/JVM parity and fallback behavior in
`ksmt-ml-tuner`.

Exit criterion: a versioned bundle produces the same validated custom cvc5
patch in Python and JVM for every golden vector.

### Production-boundary note

An inference-only predictor would have a different production boundary from
the active per-workload tuning system: it would deploy a frozen feature
projection and model bundle instead of the search loop and real replay
evaluator.

It must therefore remain an explicit later choice, not an implicit requirement
of steps 1–9.

## Current next action

Canonical step 4 is complete. The final seed-17 smoke evaluated five unique
default-centred Random Search candidates in 193.43 active seconds: four
candidates completed all six profiles with zero wrong answers, known-status
timeouts, replay failures, or recorded failures; one candidate was correctly
censored after the third profile made eligibility impossible. All four complete
results passed runtime validation and cache SHA-256 verification. The append-only
trace is contiguous, the checkpoint is terminal with no pending trial, and an
identical invocation exits idempotently without launching cvc5.

No candidate improved the default in this five-sample infrastructure smoke;
the best observed normalized cost was 1.003848. This is not an optimizer-quality
claim. Proceed to canonical step 5: integrate SMAC3 with the same trial contract,
default-centred prior, correctness gate, real evaluator, and external budgets.
