# ksmt-ml

`ksmt-ml` is the offline experimentation and training repository for a model
that predicts cvc5 parameters from a representative incremental KSMT workload.
It is not a production JVM dependency. Its deployable result is a versioned
ONNX model bundle consumed by `ksmt-ml-tuner`.

## MVP lifecycle

KSMT defines the only public workload profile: `KSolverProfile`. The same
versioned feature projection must be used while preparing training data and in
the production JVM library.

```text
offline, before deployment

KSolverProfile samples
    -> deterministic feature projection
    -> labelled examples (features, target cvc5 configuration)
    -> model training and sealed evaluation
    -> ONNX export and cross-runtime golden vectors
    -> versioned model bundle

runtime, inside the application

KSolverProfile
    -> ksmt-ml-tuner
    -> one pre-trained ONNX inference
    -> validated cvc5 configuration patch
```

The MVP does not train or refine a model in the application. Runtime inference
does not replay a workload, run an optimizer, or have a tuning-time budget.
How labelled configurations are obtained is part of offline dataset preparation
and is deliberately not a production interface.

## Repository split

| Repository | Responsibility |
|---|---|
| KSMT | `KSolverProfile`, future profiling wrapper, configuration patch, and generic tuner interface |
| `ksmt-ml-tuner` | Production feature projection, pre-trained ONNX inference, decoding, cvc5 validation, and fallback |
| `ksmt-ml` | Offline datasets, training, evaluation, benchmarks, and ONNX bundle export |

The application applies a recommended patch while constructing a fresh cvc5
solver for a later workload run. The MVP never reconfigures an active
incremental solver.

## Stable handoff

The stable boundary of this repository is the model bundle rather than its
experimental Python API. A bundle contains the ONNX graph, a manifest, schema
identifiers, supported cvc5 versions, checksums, and golden input/output
vectors. The schemas under `schemas/` define that handoff.

Training implementations may change freely as long as the exported bundle
remains compatible and the evidence gate is satisfied.

## Evidence path

1. Collect representative profiles and target cvc5 configurations.
2. Freeze the profile, feature, parameter-space, dataset, and split versions.
3. Train simple supervised baselines before increasing model complexity.
4. Compare the predictor with default cvc5, best-fixed, and deterministic
   heuristic baselines on grouped holdout workloads.
5. Export ONNX only when predicted configurations preserve correctness and
   improve the agreed workload objective.
6. Verify parity between the training runtime and the JVM ONNX adapter.

TSA is a candidate workload source because symbolic execution produces related
incremental queries. Its current solver composition uses Yices and Z3, so a
reviewed cvc5 experimental adapter is required before using it for claims.

## Future extension

An online evaluator, online configuration search, or an RL policy may be added
after the pre-trained inference MVP works and has a measurable baseline. Those
components will require separate contracts and are not implied by the current
interfaces.

## Current state

| Area | Status |
|---|---|
| KSMT contracts | Interface-only draft; profiler implementation pending |
| `ksmt-ml-tuner` | Interface-only inference library skeleton |
| labelled cvc5 dataset | Not collected |
| trained model | Not implemented |
| production model bundle | Not available |

No tuning results or performance claims exist yet.

## Development

Requirements are Python 3.12 and GNU Make.

```bash
make check
make analysis-env
make onnx-env
```

The roadmap is [`PROJECT_PLAN.md`](PROJECT_PLAN.md), and evaluation rules are in
[`docs/experiment-protocol.md`](docs/experiment-protocol.md).

## Data policy

Do not commit raw formulas, application traces, generated datasets, experiment
history, or large models. Small synthetic fixtures, schemas, final aggregate
reports, and golden ONNX vectors may be reviewed in Git.
