# ONNX tuner bundle contract

## Ownership

KSMT owns the observation-only `KSolverProfile` and profiler contracts.
`ksmt-ml-tuner` owns `Cvc5MlTuner`, `Cvc5TuningDecision`,
`Cvc5ConfigurationPatch`, JVM feature extraction, ONNX Runtime, decoding,
validation, and fallback. `ksmt-ml` trains and exports bundles.

## Bundle

A deployable bundle contains `model.onnx`, a manifest, feature and parameter
schemas, golden vectors, checksums, and a README. The manifest pins model and
bundle IDs, compatible profile/feature/parameter schemas, cvc5 versions, tensor
names and shapes, output encodings, objective, training provenance, evaluation
status, and fallback policy.

## Semantics

The graph is trained before deployment and receives tensors, never KSMT
objects. The production adapter validates the single profile, computes
deterministic features, invokes ONNX once, decodes parameter coordinates,
validates every option and value for the exact cvc5 version, and returns either
`UseConfiguration` or `KeepDefaults`.

The runtime adapter does not train the model, replay a workload, search
configurations, or enforce a tuning-time budget.

The parameter schema describes option domains, not complete preset
configurations. An accepted patch is intended for a fresh solver before it
receives assertions.

## Safety and versioning

Reject unknown schemas, non-finite inputs, invalid output values, incompatible
solver versions, unexpected tensors, and checksum mismatches. Never silently
coerce unsupported parameters. Changes to profile semantics, feature order,
parameter domains, model weights, or output decoding require the corresponding
new version or identifier.

No production bundle exists yet.
