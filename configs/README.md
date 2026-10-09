# Versioned configurations

This directory contains small, reviewable inputs that define datasets and
experiments. Configuration changes should be committed separately from run
outputs so a registry entry can identify the exact inputs used.

- `datasets/` records public benchmark selections, expected file counts, and
  archive checksums.
- Future solver action spaces belong in `solvers/` and must include the cvc5
  version, default configuration, and every allowed option/value.
- Future experiment specifications belong in `experiments/` and should point
  to a dataset manifest, feature schema, action-space version, split strategy,
  timeout, and random seed.

Large benchmarks, feature tables, runtime measurements, and trained models do
not belong here; their local locations are ignored according to the policies in
[`../data/README.md`](../data/README.md) and
[`../experiments/README.md`](../experiments/README.md).
