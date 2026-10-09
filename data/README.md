# Data policy

Raw SMT-LIB archives, extracted benchmarks, generated feature tables, runtime
records, and trained models are not stored in Git.

The first analysis uses the ten archives listed in
`configs/datasets/smtlib-2025-ten-archive.tsv`. Place archives under
`data/raw/smtlib-2025/archives/`, extract them into a directory whose first
level is the SMT-LIB logic, and write generated tables under `data/processed/`.

Expected extractor layout:

```text
benchmarks/non-incremental/
  QF_ALIA/<family>/<benchmark>.smt2
  QF_AUFLIA/<family>/<benchmark>.smt2
  ...
```

The committed notebook contains executed `ksmt-machsmt-inspired-v1` outputs for
inspection. Rebuilding it requires regenerating the ignored feature table,
schema sidecar, and manifest first.
