# CI plan

CI will wrap commands that already work from a fresh local clone. It should not
be the first place where build assumptions are encoded.

## Phase 1 - first development branch (implemented)

- checkout with persisted credentials disabled;
- JDK 17 setup and Gradle dependency caching;
- `./gradlew clean test` using published KSMT artifacts;
- repository hygiene check for generated directories, oversized files,
  absolute local paths, and accidental data/model artifacts;
- least-privilege `contents: read` permissions;
- concurrency cancellation for superseded branch/PR runs;
- pin third-party actions to full commit SHAs.

The workflow should run for pushes to `dev` and pull requests targeting `dev`
or `main`. `main` protection can require the same checks once the development
branch is stable.

## Phase 2 - analysis reproducibility

- create a clean Python environment from `requirements-analysis.txt`;
- validate notebook syntax and execute a tiny fixture-backed analysis;
- verify that the committed notebook has no error outputs;
- check generated report paths and required experiment-registry columns.

The full 64,432-row dataset does not belong in CI.

## Phase 3 - runtime and model pipeline

- small isolated solver smoke matrix;
- action-schema validation;
- Python model to Python ONNX parity;
- Python ONNX to Java ONNX parity;
- model-bundle manifest and checksum validation.

Security and dependency-update workflows should be added after the build and
test workflow is stable, following the same minimal-permission and pinned-action
rules used by `my-cpp-tools`.
