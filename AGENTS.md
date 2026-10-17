# AGENTS.md

## Project mission

`ksmt-ml` develops and validates two provider artifacts consumed through
production contracts owned by `ksmt-ml-tuner`: versioned ONNX bundles and an
external Python search worker containing Random Search, evolutionary search,
and SMAC. Applications never depend on this repository directly.

## Architectural boundaries

1. KSMT owns only the public `KSolverProfile` and observation-only profiler
   contracts in `ksmt-profiling`.
2. Do not introduce a second copy of KSMT semantic DTOs in this repository.
3. The cvc5-specific tuner API, tuning decision, configuration patch, JVM ONNX
   adapter, search lifecycle, worker protocol, parameter validation, fallback,
   and telemetry live in the `ksmt-ml-tuner` repository.
4. This repository may emit a model bundle or a separately deployable search
   worker. Production must not depend on its notebooks, datasets, campaign
   orchestration, or experimental Python modules.
5. Profiles are observation-only. Every measured or accepted configuration is
   applied to a fresh solver before its first assertion; active solvers are not
   reconfigured.
6. The initial target is cvc5. The parameter space defines valid domains for
   both model outputs and search proposals, not a catalog of ready
   configurations.
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
  claiming either search or model improvements.
- Report profiling, search, inference, and final workload costs separately.

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

1. Inspect the relevant KSMT profile, tuner, worker-protocol, parameter-space,
   and model-bundle contracts.
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
