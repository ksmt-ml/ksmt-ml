# Contributing

Thank you for improving `ksmt-ml`. Changes are reviewed against two standards:
software correctness and research reproducibility. A faster result is not
accepted if its dataset, feature, action-space, or evaluation provenance cannot
be recovered.

## Before making a change

1. Read the relevant contract under `docs/`.
2. Check [`PROJECT_PLAN.md`](PROJECT_PLAN.md) for the current milestone and
   acceptance gate.
3. For claim-bearing or costly work, register an experiment in
   [`experiments/registry.csv`](experiments/registry.csv) before collecting
   results.
4. Keep generated corpora, feature tables, runtime shards, and large models out
   of Git.

## Development setup

The normal JVM build uses published dependencies:

```bash
make check
```

To test against a KSMT source checkout without assuming a directory layout:

```bash
./gradlew -PksmtSourcePath=/path/to/ksmt test
```

ONNX bundle changes require the pinned Python environment and both validators:

```bash
make onnx-env
make onnx-validate
./gradlew test
```

Dataset-analysis changes use:

```bash
make analysis-env
make notebook
```

The notebook command requires the ignored generated feature table, schema
sidecar, and extraction manifest described in [`data/README.md`](data/README.md).

## Contract changes

The following identifiers are independent and immutable once their artifacts
are used:

- `dataset_id`: benchmark selection and family metadata;
- `feature_schema`: feature order and extraction semantics;
- `action_space`: solver version and allowed configurations;
- model or bundle ID: preprocessing, model, tensor interface, and evidence.

Do not reuse an identifier after changing its meaning. Update the contract,
fixtures, validators, and documentation together. A binary ONNX graph is never
reviewed by itself: its manifest, checksums, golden vectors, generator, and
cross-runtime tests are part of the same change.

## Pull requests

Keep each pull request focused and include:

- the observable change and its motivation;
- affected contract identifiers, or `None`;
- exact validation commands;
- an experiment-registry reference when research evidence changes;
- limitations and any explicit fallback behavior.

All relevant tests must pass. Review the final diff for local paths, secrets,
large generated artifacts, accidental notebook data, and unrelated edits.
Do not add a second roadmap: all scheduled work and open project decisions
belong in [`PROJECT_PLAN.md`](PROJECT_PLAN.md).

## Commit and review hygiene

- Use clear imperative commit messages.
- Keep formatting-only changes separate from semantic contract changes.
- Never rewrite or delete completed experiment provenance.
- Treat solver wrong answers, crashes, timeouts, and exclusions as results to
  record, not noise to hide.
- Prefer deterministic generators and small golden fixtures over checked-in
  generated datasets.

By contributing, you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE).
