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

Status: complete. The contracts are merged in the main/master branches; demo
branches remain reference implementations and experiment infrastructure.

Finalize and merge the observation-only KSMT profile API, the tuner inference
and search APIs, the external-worker protocol, and the TSA launch/evaluator
interfaces. Keep implementations out of those interface commits. Preserve the
current experiments on `dev/litvinovmitch11/demo` branches as reference material.

Exit criterion: the four repositories agree on ownership, identifiers,
lifecycle, cancellation, failure, and fresh-solver semantics without duplicate
public DTOs.

## Phase 1 — infrastructure implementation

Status: demonstrated by the reference implementation, not yet productionized.

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

Status: RS, ES, and SMAC share a real evaluator in the demo implementation;
protocol packaging and the production host process remain open.

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

Status: the first claim-bearing pilot is complete, but its performance gate
failed. The infrastructure is replayable and correctness-safe; generalizable
speedup has not been established.

Freeze a cvc5-version-specific parameter space, workload groups, correctness
oracle, objective, seeds, budgets, timeouts, and repetitions. Compare default
cvc5, deterministic Random Search, evolutionary search, and SMAC under equal
host-enforced budgets. Keep raw run history outside Git and publish only frozen
manifests and aggregate evidence.

Exit criterion: search results are replayable from recorded provenance and no
retained configuration changes solver correctness.

### Phase 3A — harden the evidence design

Status: in progress. The v0 workload-corpus and multi-seed campaign manifests,
leakage validation, portable campaign workspace, equal-budget run validation,
all-seed qualification (including explicit no-feasible-candidate outcomes),
full-development-workload finalist filtering, multi-seed anytime aggregation,
randomized paired confirmation, default-control noise path, and grouped
bootstrap summary are implemented and covered by the repository check.
Materializing a real corpus, running all configurator seeds, and executing
sealed confirmation are campaign operations performed outside Git.

Before another long search, implement and freeze:

- a manifest of independent workload groups with development, validation, and
  sealed-test partitions;
- multiple configurator seeds under equal wall-clock and evaluation budgets;
- seeded randomized interleaving of default/candidate confirmation trials;
- grouped paired statistics, confidence intervals, a measured system-noise
  floor, and a predeclared practically significant gain;
- anytime incumbent traces by wall-clock and real solver evaluations;
- default, best-fixed-on-development, and oracle-best-observed diagnostics;
- replay-to-full-TSA correlation checks on multiple candidates.

Exit criterion: the campaign can distinguish optimizer improvement from host
drift, selection bias, and a lucky configurator seed.

Post-confirmation v0 status: the infrastructure passed this gate, but the gain
hypothesis did not. Random seed 17 was correct and statistically neutral;
random seed 23 accelerated one workload but produced deterministic wrong
answers on another. The opened test corpus is now diagnostic. An exhaustive
option-subset ablation and an earlier correctness screen precede the next
campaign, which must use a new sealed test corpus.

### Phase 3B — qualify cheaper evaluations

Evaluate the same candidate set on short prefixes or representative shards,
full replay, and fresh TSA runs. Report rank correlation, top-k recall, and the
rate at which a candidate changes from improvement to regression. Enable
staged evaluation, adaptive capping, Successive Halving, or a SMAC budget only
when the cheap fidelity preserves the decisions that matter at full fidelity.

Exit criterion: every early-stopping rule has measured predictive value and
cannot silently eliminate most full-fidelity winners.

### Phase 3C — compare improved configurators

Keep deterministic Random Search as the control. Improve ES with domain-aware
sampling and racing, and use SMAC intensification with instances, seeds, and
explicit censored observations. Compare all configurators using the same
frozen inputs, multiple seeds, wall-clock limits, and final full-fidelity
confirmation.

Exit criterion: either at least one procedure passes the sealed solver-level
gain gate, or the experiment produces a reproducible negative conclusion about
the current parameter space, objective, and workload corpus.

### Phase 3D — prove end-to-end value

On development workloads, measure budget splits such as `1/99`, `3/97`,
`5/95`, `10/90`, and `20/80`. Freeze one policy, then compare on fresh sealed
TSA launches:

```text
profile capture + tuning + execution with chosen patch
    versus
full execution with default cvc5
```

Exit criterion: zero correctness regressions and a positive predeclared paired
effect for total TSA wall-clock or another frozen TSA-level objective.

## Phase 4 — optional ONNX provider

Status: blocked on evidence, not on API design. Do not train a release model
from a single workload or a single configurator seed.

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
- final end-to-end TSA objective and tuning-budget accounting;
- worker executable packaging and sandbox policy;
- checkpoint retention and compatibility policy;
- model release gate, if the ONNX path is pursued;
- artifact publication and versioning process.

For the current solver-level campaign, the replay objective is fixed as
`workload-total-effective-cost-log-speedup-v0`. Effective costs are summed over
profiles; SMAC uses baseline-cost-weighted instances so it optimizes the same
whole-workload quantity as Random Search and evolution.
