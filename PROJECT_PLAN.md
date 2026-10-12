# Project plan

This is the single source of truth for planned work in `ksmt-ml`. Contract
documents describe behavior that already exists; `RESEARCH.md` records durable
decisions and evidence; the experiment registry records individual runs. Future
milestones, dependencies, exit criteria, and open decisions belong here.

## Objective

Build and evaluate a reproducible policy that selects a correctness-preserving
cvc5 configuration for each SMT-LIB benchmark, then deliver the validated
policy as a versioned ONNX bundle suitable for KSMT integration.

The project is evidence-gated:

```text
trusted features
    -> reviewed benchmark cohort and family split
    -> versioned cvc5 action space
    -> isolated runtime matrix
    -> Default / SBS / VBS headroom gate
    -> learned baseline
    -> sealed-holdout evaluation
    -> production integration
```

Work must not skip the headroom gate. Prior work motivates the approach but
does not establish that the current dataset, solver version, or action space is
learnable.

## Current state

| Area | Status | Evidence |
|---|---|---|
| Repository foundation | Complete | Gradle wrapper, Apache-2.0 license, CI, hygiene checks, experiment registry |
| Feature schema | Complete | Frozen `ksmt-machsmt-inspired-v1` contract and golden fixtures |
| Dataset extraction | Complete for the ten-archive study | 64,432 inputs, 64,341 complete vectors, 91 syntax-only vectors, zero lost inputs |
| Dataset analysis | Complete for E0004 | Executed notebook, committed figures, and dataset report |
| ONNX interface | Interface prototype complete | Checksummed bundle, Python/JVM golden batch, 30-option vocabulary, JVM decoder |
| Pilot split | Not frozen | Candidate logics identified; exact family assignment remains to be reviewed |
| cvc5 action space | Not frozen | Candidate option vocabulary exists; deployable configurations do not |
| Runtime matrix | Not started | Isolated runner and runtime labels are absent |
| Learned selector | Not started | Blocked by action-space headroom evidence |

The committed ONNX graph is deliberately non-trained. It validates the
integration boundary and must not be reported as a solver-performance result.

## Milestone 1 — freeze the pilot cohort

Use `QF_ALIA`, `QF_AUFLIA`, and `LIA` as the initial candidates. Review family
counts, concentration, extraction failures, status balance, exact-content
duplicates, and likely near-duplicate families.

Deliverables:

- a versioned cohort manifest;
- a deterministic `(logic, family)` development/holdout assignment;
- a written eligibility policy for the 91 syntax-only rows;
- a frozen primary metric, timeout, repetition policy, and uncertainty method;
- a pre-registered experiment row before runtime labels are collected.

Exit criterion: the split contains enough independent families for grouped
development evaluation and a sealed holdout, with all exclusions accounted for.

## Milestone 2 — define and measure the cvc5 action space

Construct a small, reviewed action set from the 30-option vocabulary in the
ONNX contract. Do not enumerate the Cartesian product. Every action must have a
stable ID, include an explicit default configuration, and use only values
supported by the pinned cvc5 version.

Implement an isolated benchmark runner that executes every
`(benchmark, action, repetition)` in a separate process with a parent-enforced
wall-clock timeout. Records are append-only and distinguish `ok`, `timeout`,
`unknown`, `crash`, `invalid_config`, `wrong_answer`, and infrastructure
failure.

Deliverables:

- versioned action-space manifest;
- smoke validation for every action;
- isolated runner with bounded concurrency and resumable shards;
- environment and solver-version capture;
- correctness validation against expected statuses;
- complete pilot runtime matrix.

Exit criterion: no retained action produces a wrong answer, all planned cells
are accounted for, and the matrix is reproducible from recorded manifests.

## Milestone 3 — establish selection headroom

Calculate on development families:

- cvc5 default;
- single best fixed configuration (SBS), selected on training data only;
- virtual best configuration (VBS), used only as an oracle upper bound;
- solved count, timeout count, family-macro and benchmark-micro PAR;
- family-bootstrap confidence intervals.

The numerical threshold is frozen before labels are inspected. The current
candidate gate is:

- zero wrong answers from retained actions;
- VBS improves over `min(Default, SBS)` by at least 5% PAR-10;
- the 95% family-bootstrap interval excludes zero improvement;
- at least two non-default actions win on non-trivial benchmark subsets.

Exit criterion: either the gate passes and model work begins, or the action
space/data coverage is revised in a new registered experiment. A failed gate is
not repaired by fitting a more complex model.

## Milestone 4 — reproduce the MachSMT-derived baseline

The first claim-bearing selector is the empirical hardness model (EHM):

1. standardize the 157 input features using training-fold statistics;
2. generate degree-two polynomial interactions;
3. reduce to 35 principal components;
4. fit one AdaBoost PAR-2 regressor per action with 200 decision trees and
   linear loss;
5. select the action with the lowest predicted score.

All transformations, feature filtering, action pruning, and hyperparameter
selection stay inside nested family-grouped training folds. Evaluate pooled and
per-logic variants only where family counts permit. Compact feature profiles,
imputation, and alternative estimators are named variants, not silent changes
to the baseline.

Exit criterion: on the sealed holdout, the EHM improves over Default and the
training-selected SBS, closes a meaningful fraction of the VBS gap, and remains
beneficial after feature-extraction and inference overhead.

## Milestone 5 — production model bundle

Replace the hand-authored graph without changing an existing released bundle.
The trained bundle receives a new model and bundle ID and contains:

- dataset, split, feature-schema, action-space, and experiment IDs;
- preprocessing parameters and estimator weights;
- training revision, dependency versions, and random seeds;
- evaluation and eligibility reports;
- checksums and golden vectors;
- Python-to-ONNX and ONNX-to-JVM parity tolerances;
- measured extraction and inference overhead;
- explicit fallback behavior.

Exit criterion: deterministic cross-runtime parity, validated checksums,
rollback-safe loading, observable fallback, and an integration review with the
KSMT runtime owner.

## Milestone 6 — controlled expansion

Expand one axis at a time so gains remain attributable:

1. additional cvc5 configurations;
2. additional logics with adequate family coverage;
3. additional solver versions;
4. multi-solver portfolios;
5. pairwise comparators and modern tree ensembles;
6. hierarchical or multi-task models.

Likely logic order is `QF_AUFBV` and `QF_AX`, then `NIA` and `QF_FPLRA` after
coverage review. FP-heavy and NRA cohorts remain descriptive or stress-test
data until their family concentration is substantially reduced.

Every expansion repeats cohort review, split design, action-space headroom, and
sealed evaluation under a new registered experiment.

## Engineering work aligned with the milestones

### Feature extractor hardening

- isolate parser workers and enforce a parent-side timeout;
- add resumable shards for large corpora;
- promote archive manifests to explicit per-benchmark input manifests;
- record extraction time and peak memory;
- add malformed and adversarial SMT-LIB fixtures.

These changes must preserve `ksmt-machsmt-inspired-v1`. Any ordering or
semantic change creates a new feature-schema ID.

### Continuous integration

Already implemented:

- least-privilege GitHub Actions workflow;
- pinned third-party actions;
- repository hygiene, wrapper checksum, bundle checksum, and JVM tests;
- cancellation of superseded branch/PR runs.

Planned additions:

- clean Python environment and ONNX bundle validation;
- fixture-backed notebook execution and notebook error-output check;
- experiment-registry schema validation;
- action-space and small runtime-matrix validation;
- trained-model Python/ONNX/JVM parity;
- dependency and security update automation.
- resolve the deprecated Gradle `Convention` API warning before a Gradle 9
  wrapper upgrade.

The full 64,432-row dataset and large trained artifacts remain outside CI.

### Public release readiness

- keep the README and contract index current;
- keep generated small fixtures reproducible and checksummed;
- never commit raw benchmarks, feature tables, runtime shards, or large models;
- record every public research claim in a completed registry entry;
- document breaking contract changes and assign new identifiers;
- publish no trained policy without its evaluation and limitations;
- enable GitHub private vulnerability reporting before the public release.

## Experiment sequence

| Experiment | Purpose | Gate |
|---|---|---|
| E0002 | Provisional feature fidelity | Superseded, retained as provenance |
| E0003 | Provisional corpus extraction | Superseded, retained as provenance |
| E0004 | Frozen-v1 extraction and data review | Complete |
| E0005 | Review and freeze cvc5 action catalog | Required before runtime collection |
| E0006 | Collect isolated cvc5 runtime matrix | Required before headroom analysis |
| E0007 | Default/SBS/VBS headroom | Must pass before learned-model claims |
| E0008 | MachSMT-derived EHM baseline | Starts only after E0007 passes |
| E0009+ | Controlled model/action/logic expansion | One axis per registered experiment |

The machine-readable status of each run lives in
[`experiments/registry.csv`](experiments/registry.csv).

## Open decisions

The following values must be resolved and frozen in the relevant experiment,
not chosen after observing holdout results:

- exact cvc5 configurations in `cvc5-pilot-v1`;
- exact pilot family assignment;
- wall-clock timeout and repetition count;
- PAR-10 versus PAR-2 as the primary evaluation metric;
- eligibility policy for partial feature vectors;
- minimum practically meaningful gap closed;
- artifact storage used for large runtime matrices and trained bundles.
