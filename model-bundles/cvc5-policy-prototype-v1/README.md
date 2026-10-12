# cvc5 policy ONNX interface prototype v1

This bundle is an executable contract fixture for infrastructure integration.
It is intentionally not trained and makes no solver-performance claim.

## Files

- `model.onnx`: deterministic batched ONNX graph;
- `manifest.json`: tensor contract, option encodings, and action catalog;
- `test-vectors.json`: four-row golden batch and expected decisions;
- `sha256sums.txt`: integrity hashes for the three machine-readable artifacts.

`manifest.json` conforms to
[`schemas/onnx-bundle-v1.schema.json`](../../schemas/onnx-bundle-v1.schema.json).

## Contract

Input:

```text
features: float32[batch, 157]
```

The caller must supply complete, finite, non-negative
`ksmt-machsmt-inspired-v1` vectors. Schema identity is carried by the bundle
and caller protocol, not by the tensor itself.

Outputs:

```text
action_scores: float32[batch, 4]
action_id:     int64[batch]
config_values: int64[batch, 30]
config_mask:   bool[batch, 30]
```

For every row and parameter index, set the option only when `config_mask` is
true. Decode `config_values` through the corresponding `parameters` entry in
`manifest.json`. The JVM `Cvc5OptionDecoder` performs this operation and
returns a `Cvc5SolverConfiguration` containing a `Map<String, String>` ready
for `setCvc5Option`. `action_id` is the equivalent catalog-level
representation.

Infrastructure code should normally instantiate `Cvc5OnnxPolicy` with this
directory and call `predict(rows)`. That entry point validates the model hash,
runs inference, decodes the outputs, and returns solver configurations.

```kotlin
Cvc5OnnxPolicy(bundleDirectory).use { policy ->
    val configuration = policy.predict(listOf(features)).single()
    solver.configure {
        configuration.options.forEach { (name, value) ->
            setCvc5Option(name, value)
        }
    }
}
```

The 30 positions are grouped as follows:

- general search: 6;
- arithmetic: 7;
- arrays: 5;
- bit-vectors: 6;
- quantifiers: 6.

The current four fixture actions intentionally set at most one option. The
larger vocabulary freezes the infrastructure format; it is not evidence that
all values or combinations are useful.

The committed graph applies `log1p` to the input and then uses hand-authored
linear weights to exercise all four actions. A bundle that changes the internal
graph, action count, or parameter catalog receives a new bundle ID; compatible
bundles preserve the four tensor roles above.

## Validation

```bash
make onnx-env
make onnx-validate
./gradlew test
```

Regenerate the deterministic artifact with `make onnx-prototype`. Never edit
the binary, manifest, golden vectors, or checksums independently.
