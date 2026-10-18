# ksmt-ml

ksmt-ml develops and validates cvc5 tuning providers. Production applications
use the stable JVM APIs from ksmt-ml-tuner and never depend on this repository
directly.

Two provider artifacts are planned:

1. a versioned ONNX bundle for one-shot inference from KSolverProfile;
2. a separately deployable Python search worker providing Random Search,
   evolutionary search, and SMAC through the tuner worker protocol.

The main branch owns provider-side schemas, documentation, and handoff
requirements; it does not contain either production provider artifact. The
`dev/litvinovmitch11/demo` branch additionally preserves experimental
evaluator, search, persistence, and campaign implementations as non-production
reference code.

## System boundary

    KSMT
        KSolverProfile and KSolverProfiler contracts

    TSA or another application
        workload capture and Cvc5TrialEvaluator implementation

    ksmt-ml-tuner
        production JVM API, validation, lifecycle, fallback, telemetry
        ONNX adapter and external-worker host implementations

    ksmt-ml
        ONNX bundle producer
        Python RS / ES / SMAC worker producer
        experiments, evidence, and conformance fixtures

The application applies an accepted configuration only while constructing a
fresh cvc5 solver, before its first assertion. Active solvers are never
reconfigured.

## ONNX provider

The model path is inference-only at runtime:

    KSolverProfile
        -> deterministic versioned feature projection
        -> one pre-trained ONNX inference
        -> decoded Cvc5ConfigurationPatch
        -> parameter-space validation
        -> Cvc5ValidatedConfigurationPatch

A deployable bundle contains the graph, manifest, feature and parameter
schemas, checksums, and cross-runtime golden vectors. The current
cvc5-tuner-bundle-v0 and cvc5-parameter-space-v0 schemas define the producer
side of that handoff.

There is no trained model or production bundle yet.

## Search provider

The search path reuses a Python optimizer without exposing Python APIs to a JVM
application:

    tuner host -> ASK
    search worker -> PROPOSAL
    tuner host validates the proposed patch
    application evaluator receives Cvc5ValidatedConfigurationPatch
    application evaluator -> TELL observation
    search worker -> next proposal or RESULT

The canonical cvc5-search-worker-v0 protocol is owned by ksmt-ml-tuner. This
repository will package a worker implementing that protocol. The worker owns
only algorithm state and proposal selection. The tuner host remains
authoritative for budgets, cancellation, process isolation, correctness,
artifacts, validation, and fallback.

The worker may reuse the experimental RS, ES, and SMAC implementations from the
demo branch after they are separated from TSA execution and hardened against
the protocol conformance suite.

## Stable and experimental surfaces

Stable handoffs:

- versioned model-bundle and parameter-space documents;
- the tuner-owned external worker protocol;
- conformance fixtures and golden vectors;
- final aggregate evidence with frozen provenance.

Not production interfaces:

- notebooks and training modules;
- experiment campaign scripts;
- raw run directories and caches;
- direct TSA process invocation;
- internal Python strategy or persistence classes.

## Current state

| Area | Status |
|---|---|
| KSMT profiling API | contract defined in KSMT |
| tuner inference/search API | contract defined in ksmt-ml-tuner |
| TSA launch/evaluator API | contract defined in TSA |
| RS / ES / SMAC worker | experimental reference exists only on demo |
| ONNX model and bundle | not implemented |
| performance claims | none |

## Development

Requirements are Python 3.12 and GNU Make.

    make check
    make analysis-env
    make onnx-env

The roadmap is PROJECT_PLAN.md. Claim-bearing evaluation requirements are in
docs/experiment-protocol.md.

The operational walkthrough for the experimental demo branches is in
docs/demo-end-to-end-ru.md.

## Data policy

Do not commit raw formulas, application traces, generated datasets, tuning run
history, caches, or large models. Small synthetic fixtures, schemas,
conformance messages, final aggregate reports, and golden vectors may be
reviewed in Git.
