# Project plan

This file is the single roadmap for `ksmt-ml`.

## Objective

Deliver interchangeable cvc5 tuning providers behind the stable
`ksmt-ml-tuner` JVM contracts:

1. a budgeted external worker that reuses Random Search, evolutionary search,
   and SMAC;
2. an optional pre-trained ONNX bundle for one-shot inference.

`ksmt-ml` owns provider artifacts, experiments, conformance fixtures, and
evidence. It does not own application workload execution or the production JVM
facade.

```text
KSMT profile contract
    -> TSA workload capture and trial evaluation
    -> ksmt-ml-tuner lifecycle, validation, budget, and fallback
        -> Python RS / ES / SMAC worker
        -> or ONNX bundle
```

## Phase 0 — freeze cross-repository contracts

Finalize and merge the observation-only KSMT profile API, the tuner inference
and search APIs, the external-worker protocol, and the TSA launch/evaluator
interfaces. Keep implementations out of those interface commits. Preserve the
current experiments on `dev/litvinovmitch11/demo` branches as reference material.

Exit criterion: the four repositories agree on ownership, identifiers,
lifecycle, cancellation, failure, and fresh-solver semantics without duplicate
public DTOs.

## Phase 1 — infrastructure implementation

The infrastructure team implements:

- bounded KSMT profile capture without changing solver behavior;
- the tuner host, validation, budget, cancellation, checkpoint, fallback, and
  telemetry mechanisms;
- TSA workload capture and a trial evaluator that creates an isolated fresh
  solver for every measured configuration;
- process isolation and protocol-conformance checks for external workers.

Exit criterion: a deterministic fake worker can complete, cancel, fail, and
resume a search through the production host, while invalid recommendations
fail closed.

## Phase 2 — promote the search worker

Separate the experimental RS, ES, and SMAC algorithms on the demo branch from
TSA process control and persistence. Package them behind
`cvc5-search-worker-v0`, using standard input/output only for protocol messages
and standard error for diagnostics.

The worker owns proposal selection and optimizer-native state. The tuner host
remains authoritative for budgets, trial identity, correctness results,
artifacts, cancellation, and process lifetime.

Exit criterion: all three algorithms pass the same protocol suite and can be
selected without changing the JVM application API.

## Phase 3 — reproducible search evidence

Freeze a cvc5-version-specific parameter space, workload groups, correctness
oracle, objective, seeds, budgets, timeouts, and repetitions. Compare default
cvc5, deterministic Random Search, evolutionary search, and SMAC under equal
host-enforced budgets. Keep raw run history outside Git and publish only frozen
manifests and aggregate evidence.

Exit criterion: search results are replayable from recorded provenance and no
retained configuration changes solver correctness.

## Phase 4 — optional ONNX provider

When sufficient independent data exists, freeze a deterministic feature
projection and train supervised baselines. Export a selected model only after a
sealed grouped evaluation. The bundle includes its manifest, schemas,
checksums, and Python/JVM golden vectors.

Exit criterion: a versioned bundle produces a raw `Cvc5ConfigurationPatch`
from a compatible KSMT profile, the tuner converts it to
`Cvc5ValidatedConfigurationPatch` only after parameter-space validation, and
safely falls back for every incompatible or malformed input.

## Phase 5 — release readiness

Exercise both providers through the same tuner facade and TSA launch path.
Document supported cvc5 versions, deployment artifacts, resource limits,
telemetry, upgrade rules, and rollback behavior. A deployment may ship either
provider or both.

Exit criterion: infrastructure can replace either provider implementation
without changing KSMT or TSA public interfaces.

## Open decisions

- first supported cvc5 version and parameter domains;
- workload manifest and correctness oracle;
- primary objective and multi-instance aggregation;
- worker executable packaging and sandbox policy;
- checkpoint retention and compatibility policy;
- model release gate, if the ONNX path is pursued;
- artifact publication and versioning process.
