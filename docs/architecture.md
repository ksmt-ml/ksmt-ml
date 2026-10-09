# Architecture

## Current pipeline

```text
SMT-LIB file
    |-- lightweight syntax scan -------- commands, declarations, sort tokens
    |-- KZ3SMTLibParser -> KSMT AST ----- structural and operator statistics
    `-- source metadata ----------------- byte size and source identity
                         |
                         v
          ksmt-machsmt-inspired-v1
                         |
              JSON or CSV/CSV.GZ
                         |
                 analysis notebook
```

The syntax scanner deliberately does not implement a second SMT parser. KSMT is
the semantic source of truth for structural features; the scanner only preserves
source constructs that assertion parsing cannot retain.

## Repository boundaries

### Kotlin/JVM

- owns the feature schema and extraction semantics;
- parses SMT-LIB through KSMT;
- emits stable machine-readable records;
- will later own solver execution and Java ONNX parity checks.

### Python analysis

- consumes versioned JVM-produced feature tables;
- performs data-quality analysis, splitting, training, and evaluation;
- must not independently redefine production feature semantics;
- will later export a complete model bundle rather than a bare ONNX file.

### KSMT upstream

`ksmt-ml` uses published KSMT artifacts by default. `-PuseLocalKsmt=true`
enables a sibling source checkout for integration work. Stable extractor and
inference components may eventually move into KSMT, but experimental code stays
here until its contracts are validated.

## Versioned contracts

The project will maintain four independent identifiers:

- `dataset_id`: benchmark selection and family metadata;
- `feature_schema`: ordered model inputs and extraction semantics;
- `action_space`: solver version plus allowed configurations;
- `model_id`: trained estimator, preprocessing, and evaluation evidence.

Changing one contract does not silently reuse artifacts produced for another.
Every batch feature table also carries a benchmark-content hash per row and a
completion manifest with input, output, schema, and code-revision provenance.

## Future runtime pipeline

```text
benchmark manifest
    -> isolated (problem x action x repetition) run plan
    -> append-only run shards
    -> correctness validation and aggregation
    -> Default / SBS / VBS / PAR baselines
    -> family-grouped model training
    -> versioned ONNX bundle
    -> Python/JVM parity test
```

The future runner must survive native crashes and enforce an external timeout;
`solver.check(timeout)` alone is insufficient for process isolation.
