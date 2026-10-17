# Contributing

`ksmt-ml` is reviewed for research reproducibility and compatibility with the
KSMT workload-profile contract.

## Before making a change

1. Read the relevant contract under `docs/` and the current milestone in
   [`PROJECT_PLAN.md`](PROJECT_PLAN.md).
2. Freeze the workload manifest, split, objective, parameter space, training
   hyperparameters, and seed before a claim-bearing or costly experiment.
3. Keep raw traces, generated profile tables, run history, and large models out
   of Git.
4. Keep the production JVM facade and workload execution in their owning
   repositories. This repository may contain provider workers, training,
   evaluation, export, and small artifact-validation tools.

## Development setup

Create only the environment needed for the current work:

```bash
make analysis-env
make onnx-env
make check
```

Do not add a dependency until code in the repository uses it. Pin versions and
record them with every claim-bearing run.

## Contract changes

The following identifiers are independent once artifacts use them:

- workload dataset/manifest ID;
- KSMT profile schema;
- profile-feature projection schema;
- parameter-space ID and target solver version;
- model/bundle ID;
- search-worker protocol and algorithm ID.

Do not reuse an identifier after changing its meaning. Update schemas, fixtures,
validators, documentation, and golden vectors together. A binary ONNX graph is
never reviewed without its manifest, checksums, evaluation evidence, and
cross-runtime test vectors.

## Pull requests

Include the observable change, affected identifiers, exact validation commands,
an evidence report when claims change, and known limitations.
Do not create a second roadmap; planned work belongs in `PROJECT_PLAN.md`.

Review the final diff for local paths, secrets, generated data, undocumented
schema changes, and unrelated artifacts. Do not commit, push, merge, or publish
on a user's behalf unless explicitly requested.
