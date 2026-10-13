# AGENTS.md

## Project mission

`ksmt-ml` is the non-production experimentation repository for training a model
that predicts solver parameters from one representative profile of an
incremental KSMT workload. It prepares labelled data, trains and evaluates
models, and exports versioned ONNX bundles.

## Architectural boundaries

1. KSMT owns the public `KSolverProfile`, profiler, configuration patch, and
   `KSolverTuner` interfaces in one module.
2. Do not introduce a second copy of KSMT semantic DTOs in this repository.
3. Production implementations, including the JVM ONNX adapter, deterministic
   profile projection, cvc5 parameter validation, fallback, and telemetry, live
   in the `ksmt-ml-tuner` repository.
4. This repository emits a model bundle; production must not depend on its
   training or notebook code.
5. The first lifecycle profiles one representative run and applies the returned
   configuration to a fresh solver for a subsequent run. Active solvers are not
   reconfigured.
6. The initial target is cvc5. The parameter space defines the valid domains of
   model outputs, not a catalog of ready configurations.
7. Raw formulas, application traces, generated profile tables, run shards, and
   large models are not committed. Small synthetic fixtures and golden vectors
   may be committed.
8. Every schema or model artifact is versioned. Semantic changes produce a new
   identifier.

## Research integrity

- Freeze claim-bearing experiment inputs before costly collection.
- Preserve failures explicitly. Keep generated run history outside Git.
- Record profile, application, solver, KSMT, code, configuration, host, seed,
  dataset, label, and training provenance.
- Compare configurations on deterministic replay or a justified equivalent
  workload; do not infer gains from unrelated before/after periods.
- Split by independent workload/application groups, never individual checks.
- Measure current/default, best fixed, and oracle-per-workload baselines before
  training.
- Report profiling and inference overhead separately from model training cost.

## Build and validation

```bash
make check
```

For Python analysis:

```bash
make analysis-env
make onnx-env
```

## Change workflow

1. Inspect the relevant KSMT/profile and model-bundle contracts.
2. State assumptions that affect workload, lifecycle, feature, or experiment
   semantics.
3. Make the smallest coherent change and add regression coverage.
4. Run focused tests, then repository checks where applicable.
5. Review the diff for data, secrets, absolute paths, undocumented schema
   changes, and stale references to superseded designs.
6. Update `README.md` or `PROJECT_PLAN.md` when their public claims change. Do
   not create a second roadmap or commit experiment history.

Do not commit, push, merge, publish, or change remote settings unless the user
explicitly asks.
