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
| first claim-bearing replay pilot | complete; no strategy passed the holdout gain gate |
| positive performance claim | none |

## Development

Requirements are Python 3.12 and GNU Make.

    make check
    make analysis-env
    make onnx-env

The roadmap is PROJECT_PLAN.md. Claim-bearing evaluation requirements are in
docs/experiment-protocol.md.

The operational walkthrough for the experimental demo branches is in
docs/demo-end-to-end-ru.md.

The staged Russian runbook for the next real multi-seed evidence campaign is in
docs/next-multi-seed-experiment-runbook-ru.md. Its scripts stop after capture,
baseline construction, validation-only noise calibration, each configurator,
and sealed confirmation so artifacts can be reviewed between costly stages.

The next multi-workload experiment is frozen with v0 corpus and campaign
manifests. Validate them and build a deterministic paired confirmation schedule
with:

    PYTHONPATH=src python3 -m ksmt_ml.experiment_design build-corpus \
        --corpus-id <corpus-v0> --solver-version 1.3.4 \
        --parameter-space-id <space-id> \
        --grouping-key contract-family-and-revision-v0 \
        --group development <group-id> <leakage-group-id> <baseline-manifest> \
        --group validation <group-id> <leakage-group-id> <baseline-manifest> \
        --group test <group-id> <leakage-group-id> <baseline-manifest> \
        --output <corpus.json>
    PYTHONPATH=src python3 -m ksmt_ml.experiment_design validate-corpus \
        --manifest <corpus.json>
    PYTHONPATH=src python3 -m ksmt_ml.experiment_design validate-campaign \
        --manifest <campaign.json>
    PYTHONPATH=src python3 -m ksmt_ml.experiment_design build-confirmation-schedule \
        --campaign <campaign.json> --candidate <candidate-id> \
        --output <schedule.json>

The schedule keeps every matched default/candidate pair adjacent while
randomizing pair order and within-pair order from the frozen seed. It also has
an opt-in balanced mode for repeated default/default calibration. Grouped final
statistics treat a workload group—not an individual solver check—as the
independent unit.

After a campaign manifest is frozen, measure the host noise floor by executing
the same default configuration on both sides of every randomized pair:

    scripts/run-default-noise-calibration-v0.sh \
        <corpus.json> <campaign.json> <parameter-space.json> \
        <tsa-cli.jar> <output-directory>

The runner writes one report per scheduled trial, validates and reuses completed
reports after interruption, persists observations after every pair, and emits a
dedicated v0 noise summary. By default it measures 12 balanced pairs, bootstraps
the repeated pairs, reports replay-proxy, full solver, and wall-time intervals,
and fails automatically unless their noise is small relative to the campaign's
minimum useful effect. Search entrypoints verify this bound artifact and refuse
to start unless its gate passed. This calibration is required before a costly
search.

For a multi-seed comparison, materialize the frozen campaign before starting
any optimizer:

    PYTHONPATH=src python3 -m ksmt_ml.multi_seed_campaign initialize \
        --campaign <campaign.json> \
        --output-dir <campaign-workspace>

The workspace creates one explicit run slot for each combination of
`random/evolution/smac` and seed. Populate those directories through the common
`ksmt_ml.tuning run` interface. The collector rejects unfinished runs, a seed or
algorithm mismatch, a non-development baseline, and budgets different from the
campaign manifest. A finished slot without a feasible incumbent is preserved as
`NO_FEASIBLE_CANDIDATE`, rather than treated as a corrupt experiment. Only an
incumbent that successfully covered the complete development workload is
qualified for sealed confirmation; partial SMAC instance results and penalized
incumbents are retained as negative evidence but are not test candidates.

Current claim-bearing campaigns use
`workload-total-effective-cost-log-speedup-v0`: effective profile costs are
summed before the log ratio is calculated. Random Search and evolution score the
whole replay matrix directly. SMAC receives baseline-cost-weighted instance
costs whose mean is algebraically equal to the same whole-workload normalized
cost. The old unweighted profile-macro objective remains readable for artifact
compatibility but is not the default for newly prepared campaigns.

After every declared run is finished, extract all seed incumbents, build the
common sealed-test schedule, execute it resume-safely, and write the grouped and
anytime reports with:

    scripts/finalize-multi-seed-campaign-v0.sh \
        <campaign-workspace> <parameter-space.json> <tsa-cli.jar>

This records the outcome of every seed and confirms every development-qualified
seed finalist. It does not choose the best seed after seeing sealed-test
measurements. Generated run, confirmation, and raw report directories remain
outside Git.

## Data policy

Do not commit raw formulas, application traces, generated datasets, tuning run
history, caches, or large models. Small synthetic fixtures, schemas,
conformance messages, final aggregate reports, and golden vectors may be
reviewed in Git.
