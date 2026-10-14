# Project plan

This file is the single roadmap for `ksmt-ml`.

## Objective

Train a model before deployment that maps deterministic features of one
representative `KSolverProfile` to a correctness-preserving cvc5 configuration.
Export it as an ONNX bundle and demonstrate improvement over the default
configuration on independent workloads.

```text
profile contract
    -> cvc5 profiling pilot
    -> versioned feature projection
    -> labelled training dataset
    -> supervised model training
    -> sealed-workload evaluation
    -> ONNX bundle
```

Runtime training, configuration search, solver replay, RL, and tuning-time
budgets are outside the MVP.

## Milestone 0 — contracts

Review the KSMT profile and profiler interfaces and the cvc5-specific tuner,
decision, and patch contracts in `ksmt-ml-tuner`. Keep tuning policy, feature
vectors, and cvc5-specific schemas outside KSMT. The profiler implementation is
an infrastructure follow-up, not part of this repository.

Exit criterion: KSMT interfaces compile and `ksmt-ml-tuner` consumes them
without duplicating profile DTOs.

## Milestone 1 — cvc5 profiling pilot

Build a small incremental cvc5 consumer, collect the single KSMT profile, and
verify formula, assumption, scope, timing, result, solver-version, and initial
configuration fidelity. Measure profiling overhead.

TSA may become a workload source after its Yices/Z3 composition is adapted to
cvc5. Instrument the layer that represents the actual input seen by cvc5.

Exit criterion: at least one realistic workload produces a complete profile
accepted by the feature projection.

## Milestone 2 — features and labelled dataset

Freeze the feature schema and a small cvc5-version-specific output parameter
space. Prepare grouped examples containing a feature vector and a target cvc5
configuration. Record how every target was obtained, but do not make that
process part of the production API.

Use the same projection semantics for dataset generation and JVM inference.
Add golden vectors to detect Python/JVM drift.

Exit criterion: a versioned dataset can be split by independent workload or
application groups without leaking related examples into holdout.

## Milestone 3 — train supervised baselines

Start with simple predictors and compare them with default cvc5, the best fixed
configuration selected on training data, and a deterministic heuristic. Increase
model complexity only when grouped validation justifies it.

Exit criterion: at least one predictor emits values that can be decoded and
validated for the pinned cvc5 parameter space.

## Milestone 4 — sealed evaluation

Freeze all design choices and evaluate once on the grouped holdout. Reject
models that produce invalid configurations, wrong answers, or regress the
agreed objective beyond the declared tolerance.

Exit criterion: the selected predictor improves the agreed objective over the
accepted baselines while preserving correctness.

## Milestone 5 — ONNX handoff

Export the selected predictor with manifest, profile/feature/parameter-space
schema IDs, solver compatibility, checksums, and golden vectors. Validate Python
and JVM output parity. `ksmt-ml-tuner` owns runtime projection, inference,
decoding, validation, and fallback.

Exit criterion: a versioned bundle can produce a validated
`Cvc5ConfigurationPatch` from the single KSMT profile.

## Milestone 6 — end-to-end demonstration

Profile a representative run, perform one fast inference, construct a fresh
cvc5 solver with the returned patch, and repeat the workload. Report solver
statuses, failures, profiling cost, inference cost, and improvement over default
across independent workload groups.

## Future extension — adaptive tuning

After the inference-only MVP works, the system may add a workload evaluator,
online configuration search, or an RL policy that learns from measured runs.
That extension requires new lifecycle, isolation, budget, persistence, and
failure-handling contracts; none are part of the current public API.

## Open decisions

- first cvc5 version and output parameter domains;
- source and provenance of target configurations;
- TSA revision and cvc5 adapter design;
- primary objective and correctness oracle;
- minimum improvement required to export a model;
- model-bundle release and embedding process.
