# Research roadmap

## Goal

Build and evaluate a reproducible selector that chooses a solver configuration
or solver for each SMT-LIB benchmark, then provide a versioned inference bundle
that can be integrated with KSMT.

Progression is evidence-gated. A later phase starts only after the previous
phase's exit criterion is met.

## Phase 0 - trustworthy inputs (complete)

Deliver `ksmt-machsmt-inspired-v1`, golden fixtures, dataset identity,
checksummed extraction, CI, and an experiment registry.

Exit criterion: all contract tests pass and the selected SMT-LIB corpus is
extracted with zero lost inputs. This criterion is met for the current corpus.

## Phase 1 - data review and pilot cohort (next)

Start with `QF_ALIA`, `QF_AUFLIA`, and `LIA`. In the current sample each has at
least five benchmark families and no family contributes more than 50% of its
logic. Analyze:

- benchmark/family counts and concentration;
- SAT/UNSAT/UNKNOWN and extraction-status balance;
- missing, constant, duplicate, sparse, and heavy-tailed features;
- cross-logic and cross-family feature shift;
- content hashes and near-duplicate risk;
- feasibility of family-level development and sealed holdout splits.

This phase is descriptive. It must not use solver runtime labels to tune the
feature schema or choose the final holdout.

Exit criterion: a reviewed cohort manifest and frozen family split with enough
independent groups for nested validation.

## Phase 2 - runtime matrix and action-space headroom

Begin with one solver, cvc5, and a small versioned set of semantically safe
configurations. Run every `(benchmark, action, repetition)` in an isolated
process with an external timeout and correctness checking.

Measure Default, SBS, and VBS before training. Prune invalid or dominated
actions using development families only.

Exit criterion: no retained action produces a wrong answer, and VBS shows the
pre-registered, statistically supported PAR improvement over both Default and
SBS. If there is no headroom, revise the action space instead of training.

## Phase 3 - MachSMT baseline

Reproduce the paper's empirical hardness model (EHM) as the first learned
baseline:

1. standardize features using training-fold statistics;
2. generate degree-two polynomial interaction features;
3. reduce to 35 principal components;
4. train one AdaBoost runtime regressor per action with 200 decision-tree
   estimators and linear loss, using the action's PAR-2 score as target;
5. select the action with minimum predicted runtime.

Train both a pooled EHM and per-logic EHMs where group counts permit. Keep all
preprocessing inside each training fold. Unlike the paper's random five-fold
evaluation, use nested family-grouped validation and evaluate once on sealed
families. The paper baseline uses the complete 157-feature vector; compact
profiles and missing-value handling are recorded as separate variants.

Exit criterion: the EHM improves over Default and training-selected SBS on the
holdout, closes a meaningful fraction of the VBS gap, and remains beneficial
after feature plus inference overhead.

## Phase 4 - broaden the benchmark scope

Add logics in evidence-driven batches. A logic enters the primary evaluation
only when it has enough independent families and acceptable concentration;
otherwise report it as a stress test. Re-run dataset review, split design,
headroom measurement, and model evaluation for every added cohort.

Likely order:

1. `QF_AUFBV` and `QF_AX` after increasing family coverage;
2. `NIA` and `QF_FPLRA` after reducing dominant-family risk;
3. FP-heavy and NRA cohorts only after acquiring substantially more diverse
   families.

## Phase 5 - broaden actions, solvers, and models

Expand one axis at a time so improvements remain attributable:

- more cvc5 configurations;
- additional solver versions;
- multi-solver portfolios;
- MachSMT pairwise comparators;
- modern tree ensembles and calibrated classifiers;
- hierarchical and multi-task models across logics.

Every candidate is compared against the frozen MachSMT EHM baseline under the
same splits, runtime matrix, penalties, and uncertainty analysis. More complex
models are accepted only for reproducible out-of-family gains.

## Phase 6 - deployment

Freeze preprocessing, feature order, action space, model parameters, and
evaluation evidence in one model bundle. Verify Python/JVM parity, export ONNX
where supported, benchmark extraction/inference overhead, and only then propose
KSMT integration.

Exit criterion: deterministic end-to-end parity tests and a rollback-safe KSMT
integration design.
