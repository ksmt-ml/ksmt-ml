# Feature extractor roadmap

The extractor contract is now frozen as `ksmt-machsmt-inspired-v1`; see
[feature-schema.md](feature-schema.md).

## Complete

- 145 unique non-string grammar counts without reserved or duplicate slots;
- 11 defined KSMT AST statistics and UTF-8 source size;
- single-file JSON and deterministic dataset CSV/CSV.GZ extraction;
- dataset/benchmark identity, schema sidecar, hashes, and completion manifest;
- syntax-only fallbacks and explicit failures;
- seven theory fixtures plus exhaustive schema wiring tests;
- full 64,432-file validation run.

## Operational hardening still required

- isolate parsers/workers in subprocesses and enforce a parent-side timeout;
- add resumable shards for large corpora;
- promote the archive manifest to an explicit per-benchmark input manifest;
- add extraction-time and peak-memory telemetry;
- test malformed and adversarial SMT-LIB inputs.

These items do not change the feature meaning. Any future semantic change must
create `v2`.
