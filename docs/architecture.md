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
          +--------------+--------------+
          |                             |
   JSON or CSV/CSV.GZ             float32[batch, 157]
          |                             |
  analysis notebook              ONNX policy bundle
                                        |
                          action + encoded option tensors
                                        |
                              JVM manifest decoder
                                        |
                              cvc5 option map
```

The syntax scanner deliberately does not implement a second SMT parser. KSMT is
the semantic source of truth for structural features; the scanner only preserves
source constructs that assertion parsing cannot retain.

## Repository boundaries

### Kotlin/JVM

- owns the feature schema and extraction semantics;
- parses SMT-LIB through KSMT;
- emits stable machine-readable records;
- loads versioned ONNX bundles and decodes cvc5 options;
- executes JVM ONNX golden-vector parity tests.

### Python analysis

- consumes versioned JVM-produced feature tables;
- performs the current data-quality analysis;
- must not independently redefine production feature semantics;
- generates and validates the current interface bundle as one complete unit.

### ONNX policy boundary

- accepts a dynamic batch of complete 157-position `v1` vectors;
- returns action scores, an action ID, 30 encoded cvc5 option values, and an
  explicit set/unset mask;
- keeps string option names and enum decoding in a versioned JSON manifest;
- exposes a JVM decoder whose public result is a typed solver configuration
  with `Map<String, String>` options;
- rejects schema mismatches and partial/non-finite vectors at the JVM boundary;
- currently has an interface-only heuristic fixture, not a trained selector.

### KSMT upstream

`ksmt-ml` uses published KSMT artifacts by default. A source build can be
selected explicitly with the `ksmtSourcePath` Gradle property for upstream
integration testing; the repository does not assume any developer directory
layout.

## Versioned contracts

The project maintains four independent identifiers:

- `dataset_id`: benchmark selection and family metadata;
- `feature_schema`: ordered model inputs and extraction semantics;
- `action_space`: solver version plus allowed configurations;
- `model_id`: trained estimator, preprocessing, and evaluation evidence.

Changing one contract does not silently reuse artifacts produced for another.
Every batch feature table also carries a benchmark-content hash per row and a
completion manifest with input, output, schema, and code-revision provenance.

Runtime-collection and training milestones are defined only in
[`PROJECT_PLAN.md`](../PROJECT_PLAN.md).
