# ONNX solver-policy contract

## Purpose and status

`cvc5-policy-prototype-v1` freezes an executable boundary between feature
extraction, model inference, and solver configuration before runtime labels or
a trained selector exist. It lets infrastructure work validate batching,
serialization, option decoding, error handling, and JVM deployment without
pretending that the current heuristic is a research result.

The bundle status is `interface-prototype`. It is not the MachSMT baseline, is
not registered as a completed ML experiment, and must not be benchmarked as an
expected performance improvement.

The machine-readable manifest format is defined by
[`schemas/onnx-bundle-v1.schema.json`](../schemas/onnx-bundle-v1.schema.json).

## Tensor API

| Tensor | Type | Shape | Meaning |
|---|---|---|---|
| `features` | `float32` | `[batch, 157]` | Raw complete `ksmt-machsmt-inspired-v1` vectors |
| `action_scores` | `float32` | `[batch, actions]` | Diagnostic preferences; greater is preferred |
| `action_id` | `int64` | `[batch]` | Selected catalog entry |
| `config_values` | `int64` | `[batch, 30]` | Encoded cvc5 option values |
| `config_mask` | `bool` | `[batch, 30]` | Whether each cvc5 option must be explicitly set |

The batch dimension is dynamic. Feature order is immutable and identified by
`featureSchema` in `manifest.json`. The tensor does not carry the schema ID, so
every caller must check it before inference.

## Why both action and parameter outputs exist

`action_id` is the preferred high-level API: it points to a complete,
versioned action in the manifest. `config_values` plus `config_mask` provide a
lower-level interface for systems that need to materialize options without a
second table lookup and make batched integration tests explicit.

A zero in `config_values` does not mean “set the option to zero.” The option is
left at its solver default whenever the corresponding mask value is false.
Enum and boolean encodings are defined only by the manifest, never inferred by
position or hard-coded independently. The 30-position vocabulary covers six
general-search options, seven arithmetic options, five array options, six
bit-vector options, and six quantifier options. Exact names, defaults, and
encodings live in the manifest.

On the JVM, `Cvc5OptionDecoder` is the required decoding boundary. It reads the
manifest rather than duplicating option indices, verifies that the decoded
configuration matches `action_id`, and returns `Cvc5SolverConfiguration` with
a ready-to-apply `Map<String, String>`.

Infrastructure should use `Cvc5OnnxPolicy` as the bundle-level entry point. It
accepts a bundle directory, checks the model SHA-256 and schema dimensions,
runs ONNX inference, and returns decoded solver configurations from
`predict(rows)`.

## Fixed cvc5 parameter vocabulary

The vocabulary is taken from `Solver.getOptionInfo()` in the bundled cvc5
1.3.4 runtime. Defaults are recorded for review, but a masked-out position is
never explicitly set, even when its encoded value happens to equal the default.

| Index | Group | cvc5 option | Default | Allowed values |
|---:|---|---|---|---|
| 0 | general | `decision` | `internal` | `internal`, `justification`, `stoponly` |
| 1 | general | `simplification` | `batch` | `batch`, `none` |
| 2 | general | `sat-solver` | `minisat` | `minisat`, `cadical` |
| 3 | general | `deep-restart` | `none` | `none`, `all`, `input`, `input-and-prop`, `input-and-solvable` |
| 4 | general | `lemma-inprocess` | `none` | `none`, `light`, `full` |
| 5 | general | `preregister-mode` | `eager` | `eager`, `lazy` |
| 6 | arithmetic | `arith-prop` | `both` | `both`, `none`, `unate`, `bi` |
| 7 | arithmetic | `arith-rewrite-equalities` | `false` | `false`, `true` |
| 8 | arithmetic | `arith-static-learning` | `true` | `false`, `true` |
| 9 | arithmetic | `miplib-trick` | `false` | `false`, `true` |
| 10 | arithmetic | `unate-lemmas` | `all` | `all`, `eqs`, `ineqs`, `none` |
| 11 | arithmetic | `nl-ext` | `full` | `full`, `light`, `none` |
| 12 | arithmetic | `nl-cov` | `true` | `false`, `true` |
| 13 | arrays | `arrays-eager-index` | `true` | `false`, `true` |
| 14 | arrays | `arrays-eager-lemmas` | `false` | `false`, `true` |
| 15 | arrays | `arrays-optimize-linear` | `true` | `false`, `true` |
| 16 | arrays | `arrays-reduce-sharing` | `false` | `false`, `true` |
| 17 | arrays | `arrays-weak-equiv` | `false` | `false`, `true` |
| 18 | bit-vectors | `bitblast` | `lazy` | `lazy`, `eager` |
| 19 | bit-vectors | `bv-sat-solver` | `cadical` | `cadical`, `cryptominisat`, `kissat` |
| 20 | bit-vectors | `bv-eager-eval` | `false` | `false`, `true` |
| 21 | bit-vectors | `bv-gauss-elim` | `false` | `false`, `true` |
| 22 | bit-vectors | `bv-propagate` | `true` | `false`, `true` |
| 23 | bit-vectors | `solve-bv-as-int` | `off` | `off`, `bitwise`, `bv`, `iand`, `sum` |
| 24 | quantifiers | `cbqi` | `true` | `false`, `true` |
| 25 | quantifiers | `cbqi-mode` | `prop-eq` | `prop-eq`, `conflict` |
| 26 | quantifiers | `e-matching` | `true` | `false`, `true` |
| 27 | quantifiers | `finite-model-find` | `false` | `false`, `true` |
| 28 | quantifiers | `mbqi` | `false` | `false`, `true` |
| 29 | quantifiers | `miniscope-quant` | `conj-and-fv` | `conj-and-fv`, `agg`, `conj`, `fv`, `off` |

This is a candidate search vocabulary, not a claim that all Cartesian-product
combinations are valid or useful. The learned policy must select only reviewed
configurations or clusters built from this vocabulary.

## Validation and fallback

The JVM wrapper rejects:

- a feature schema other than `ksmt-machsmt-inspired-v1`;
- a row whose width is not 157;
- an empty batch;
- `NaN`, infinity, negative sentinels, and partial vectors.

It does not silently convert an invalid row into another formula. The caller
must implement and observe an explicit fallback, initially cvc5 default. This
keeps model availability and input-quality failures measurable.

The bundle contains a four-row golden batch. Python ONNX Runtime and JVM ONNX
Runtime must return identical action IDs, encoded values, and masks. CI also
checks artifact hashes and executes the JVM parity test.

## Versioning rules

Create a new bundle ID when any of the following changes:

- feature schema or feature order;
- tensor name, type, rank, or interpretation;
- parameter order or encoding;
- action catalog or solver version;
- invalid-input or selection semantics;
- trained model and preprocessing intended for deployment.

Replacing only the graph weights while claiming the same trained model also
requires a new model ID and checksum, even if the tensor interface remains
compatible.

The acceptance sequence and required evidence for a learned bundle are defined
in [`PROJECT_PLAN.md`](../PROJECT_PLAN.md). Claim-bearing evaluation follows
[`experiment-protocol.md`](experiment-protocol.md).
