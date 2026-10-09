# AGENTS.md

## Project mission

`ksmt-ml` investigates automatic SMT solver configuration for KSMT. The current
public milestone is a reproducible feature-extraction and dataset-analysis
pipeline. Runtime collection, machine-learning selection, ONNX inference, and
production KSMT integration are later milestones.

Use `README.md` for user-facing setup. Keep this file focused on stable
engineering and research rules for contributors and coding agents.

## Architectural boundaries

1. KSMT expressions are the canonical AST representation for structural
   features.
2. Source-only information may be recovered by a small SMT-LIB scanner, but
   semantic AST logic must not be independently reimplemented in Python.
3. A feature schema is immutable after data has been produced. Semantic changes
   require a new schema version.
4. Training and future JVM inference must share feature order, preprocessing,
   and action-space versions.
5. Raw SMT-LIB corpora, generated feature tables, run shards, and model
   artifacts are not committed to Git.
6. A future benchmark runner must isolate native solver failures and distinguish
   timeout, unknown, crash, invalid configuration, and wrong answer.

## Research integrity

- Register an experiment before a costly run.
- Treat run results as append-only; never hand-edit measurements.
- Record dataset ID, feature schema, action-space version, solver version,
  KSMT version, code revision, seed, timeout, and host information.
- Split related benchmarks by family, never randomly by individual file.
- Calculate Default, single best solver/configuration (SBS), and virtual best
  solver/configuration (VBS) before training a selector.
- Fit filtering and preprocessing on training folds only.
- Report failed, partial, and excluded inputs explicitly.

## Build and validation

```bash
./gradlew test
./gradlew clean test
./gradlew -PuseLocalKsmt=true test  # optional sibling KSMT checkout
```

For Python analysis:

```bash
make analysis-env
make notebook
```

## Change workflow

1. Inspect the relevant contract and tests.
2. State assumptions that affect feature or experiment semantics.
3. Make the smallest coherent change.
4. Add a regression test for changed behavior.
5. Run focused tests, then `make check` for repository-wide changes.
6. Review the final diff for generated data, secrets, absolute local paths,
   undocumented schema changes, and unrelated files.
7. Update README, roadmap, research state, or registry when their public claims
   change.

Do not commit, push, merge, publish, or change GitHub settings unless the user
explicitly asks for it.

## Definition of done

A change is complete when behavior matches the stated milestone, relevant tests
pass from the repository's own tooling, public contracts and limitations are
documented, experiment provenance is preserved, and no local datasets or
generated run artifacts are accidentally tracked.
