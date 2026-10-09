# Experiment protocol

## Principle

Do not train a selector merely because features exist. A learned policy is
justified only when a fixed, correctness-preserving configuration set has
measurable VBS headroom over both the default solver and the single best fixed
configuration (SBS) on unseen benchmark families.

Using MachSMT as prior art justifies the research direction and some feature
ideas. It does not validate this dataset, cvc5 version, action set, split, or
implementation.

## Pre-registration before runtime collection

Create a planned registry row and freeze:

- dataset manifest and benchmark hashes;
- feature schema;
- cvc5/KSMT versions and a named action-space file;
- host description, CPU allocation, warm-up policy, timeout, repetitions, and
  seeds;
- correctness oracle and policy for `unknown`/disagreement;
- family-grouped split assignment;
- primary metric, uncertainty procedure, and the numerical go/no-go threshold.

Solver actions must run in separate processes under an external wall-clock
timeout. A native crash or hang must become a record, not terminate or stall the
campaign.

## Raw runtime records

Records are append-only and include at least:

```text
experiment_id, dataset_id, benchmark_sha256, relative_path, logic, family,
feature_schema, action_space, action_id, repetition, seed, expected_status,
actual_status, correct, outcome, parse_ms, feature_ms, solver_init_ms,
assert_ms, solve_ms, total_ms, timeout_ms, host_id, solver_version,
ksmt_version, code_revision
```

`outcome` distinguishes `ok`, `timeout`, `unknown`, `crash`, `invalid_config`,
`wrong_answer`, and infrastructure failure. Never hide incorrect answers or
infrastructure failures inside a runtime penalty.

## Split and model-selection contract

The independent unit is `(logic, family)`, not a file. Duplicate or
content-identical benchmarks are resolved before splitting.

1. Reserve one or more complete families as the final holdout and seal their
   labels until every action-space and model choice is frozen.
2. On the remaining development data, use outer family-grouped folds for an
   unbiased model-selection estimate.
3. Inside each outer training fold, use inner family-grouped folds for
   hyperparameters, feature filtering, calibration, and action pruning.
4. Fit imputation, `log1p`/scaling, constant and duplicate removal, and feature
   selection inside the training fold only.
5. Select the SBS and any reduced action portfolio from training data only.

A primary per-logic claim requires at least five eligible families and no
single family above 50% of that logic's examples. On the current sample,
`QF_ALIA`, `QF_AUFLIA`, and `LIA` are the sensible first candidates; heavily
concentrated FP/NRA logics are useful stress tests, not primary generalization
evidence.

## Required baselines and metrics

- **Default:** unmodified solver configuration.
- **SBS:** one fixed action chosen on the corresponding training data.
- **VBS:** per-problem oracle over the frozen actions; an upper bound only.
- **Learned selector:** evaluated under the same timeout and penalty.

Primary metric: PAR-10 unless pre-registration chooses PAR-2. Also report
solved count, wrong answers, timeouts, median and tail runtime, feature time,
inference time, and both benchmark-micro and family-macro aggregates.

For lower-is-better PAR, report the fraction of oracle gap closed:

```text
gap_closed = (PAR(SBS) - PAR(model)) / (PAR(SBS) - PAR(VBS))
```

If the denominator is non-positive, there is no selection headroom and model
training stops. Confidence intervals use a family-level bootstrap, never an
independent-file bootstrap.

## Go/no-go gate for ML

The exact threshold must be frozen before labels are inspected. A reasonable
pilot gate is:

- no wrong answers from retained actions;
- VBS improves over `min(Default, SBS)` by at least 5% PAR-10 on the
  development outer folds;
- the 95% family-bootstrap interval for that improvement excludes zero;
- at least two non-default actions win on non-trivial numbers of benchmarks.

Failing this gate means improve the action space or data coverage, not fit a
more complex model. Passing it permits simple baselines first: decision trees,
random forests/gradient boosting, or per-action runtime regression. Neural
models are not the default for a small number of families.

## First learned baseline: MachSMT EHM

After the gate passes, the first claim-bearing learned selector is the
paper-derived empirical hardness model, not a newly invented architecture. For
each action, use its PAR-2 score as the target and fit a separate regression
pipeline:

1. standardize inputs to zero mean and unit variance;
2. generate degree-two polynomial interaction features;
3. reduce the result to 35 principal components;
4. fit AdaBoost with 200 decision-tree estimators and linear loss.

At inference, evaluate every per-action regressor and choose the action with
the minimum predicted score. Every transformation and every regressor is fit
only on the corresponding inner training fold. The baseline uses all 157 local
`v1` features and does not perform data-dependent feature selection. Rows with
partial feature extraction are excluded under a pre-registered eligibility rule
and fully accounted for; imputation, missingness indicators, or compact profiles
are named sensitivity variants, not silent changes to the paper baseline.

This reproduces the central EHM recipe reported for MachSMT, but not its random
file-level five-fold evaluation. Our estimate uses nested `(logic, family)`
grouped validation and a sealed family holdout. Report both the pooled model
and per-logic models where the number of independent groups permits them. Any
departure in estimator hyperparameters, target censoring, runtime penalty, or
preprocessing is recorded as a named variant rather than silently folded into
the baseline.

## Final evaluation

After all choices are frozen, retrain once on all development families and
evaluate once on the sealed holdout. Report the holdout regardless of outcome,
including family-level results and uncertainty. Do not return to model or
action selection after observing it without declaring a new experiment and a
new holdout.

## Completion checklist

- raw shards, manifests, split assignment, and hashes are retained;
- every wrong answer, crash, timeout, and excluded action is accounted for;
- Default/SBS/VBS and selector results use identical eligible rows;
- transformations and action selection were fit without holdout leakage;
- code revision, environment, seeds, and rerun command are recoverable;
- the report states whether the pre-registered gate passed;
- `RESEARCH.md` and the registry link the evidence and limitations.
