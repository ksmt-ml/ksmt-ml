# ksmt-ml

[![CI](https://github.com/ksmt-ml/ksmt-ml/actions/workflows/ci.yml/badge.svg)](https://github.com/ksmt-ml/ksmt-ml/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

`ksmt-ml` is a research and integration project for automatic SMT solver
configuration in [KSMT](https://github.com/UnitTestBot/ksmt). It provides a
versioned SMT-LIB feature extractor, a reproducible dataset-analysis pipeline,
and an executable ONNX interface for selecting cvc5 configurations.

The project currently answers the infrastructure question — how features and
solver options cross the JVM/ONNX boundary — but does not yet claim that a
learned policy improves solver performance.

## Status

| Component | Status |
|---|---|
| 157-position feature contract | Stable as `ksmt-machsmt-inspired-v1` |
| Single-file and batch extraction | Implemented and covered by golden fixtures |
| SMT-LIB 2025 ten-archive analysis | Complete for experiment E0004 |
| cvc5 option vocabulary | 30 typed options fixed in the prototype manifest |
| ONNX/JVM integration | Executable, checksummed interface prototype |
| Isolated solver runtime matrix | Not implemented |
| Trained configuration selector | Not implemented |
| Production KSMT integration | Not implemented |

The next milestones and their evidence gates live only in
[`PROJECT_PLAN.md`](PROJECT_PLAN.md).

## System overview

```text
SMT-LIB benchmark
    |-- source-aware token scan
    |-- KSMT AST traversal
    `-- source metadata
             |
             v
ksmt-machsmt-inspired-v1: float[157]
             |
             v
ONNX policy: float32[batch, 157]
             |
             v
encoded cvc5 configuration: values[batch, 30] + mask[batch, 30]
             |
             v
JVM decoder: Map<String, String>
             |
             v
cvc5 setOption(name, value)
```

The committed ONNX graph is a deterministic hand-authored heuristic. It exists
to validate batching, serialization, checksums, decoding, and JVM deployment;
it is not a trained baseline or a performance result.

## Requirements

- JDK 17;
- Python 3.12 for notebook and ONNX artifact tooling;
- GNU Make for the documented command aliases;
- Git for revision metadata and repository checks.

The Gradle Wrapper is committed. Normal builds use published KSMT 0.6.7
artifacts and do not require a KSMT source checkout.

## Quick start

```bash
git clone https://github.com/ksmt-ml/ksmt-ml.git
cd ksmt-ml
make check
```

`make check` runs repository hygiene validation and the complete Kotlin/JVM
test suite. It does not download the large SMT-LIB dataset or execute the
full-corpus notebook.

Show the command-line interface:

```bash
./gradlew run --args="--help"
```

## Feature extraction

### Extract one benchmark

Write a JSON record to standard output:

```bash
./gradlew run --args="--extract-features path/to/input.smt2"
```

Write the same record to a file:

```bash
./gradlew run \
  --args="--extract-features path/to/input.smt2 build/results/input.features.json"
```

The record contains the schema ID, the ordered 157 values, source identity,
and any unavailable feature IDs. Feature ordering and exact semantics are
defined in [`docs/feature-schema.md`](docs/feature-schema.md).

### Extract a dataset

The input root must contain logic directories and benchmark-family
subdirectories:

```text
benchmarks/non-incremental/
  QF_ALIA/<family>/<benchmark>.smt2
  QF_AUFLIA/<family>/<benchmark>.smt2
  LIA/<family>/<benchmark>.smt2
  ...
```

Run deterministic bounded-parallel extraction:

```bash
./gradlew run --args="--extract-dataset \
  smtlib-2025-ten-archive-v1 \
  path/to/benchmarks/non-incremental \
  data/processed/smtlib-2025-features.csv.gz \
  configs/datasets/smtlib-2025-ten-archive.tsv \
  YOUR_GIT_COMMIT \
  4"
```

Arguments after the command are:

1. dataset ID;
2. dataset root;
3. output CSV or CSV.GZ;
4. reviewed input manifest;
5. code revision recorded in provenance;
6. optional worker count.

The extractor publishes three files:

```text
smtlib-2025-features.csv.gz
smtlib-2025-features.schema.csv
smtlib-2025-features.manifest.json
```

The completion manifest is published last and contains schema, input, output,
dependency, revision, checksum, and extraction-count provenance. Output order
is deterministic. Parsing and bounded-traversal failures remain visible:

- `ok`: all 157 values are available;
- `partial`: token counts and source size are retained, while structural
  features 146–156 are missing;
- `error`: the input remains represented with an explicit failure record.

The command exits non-zero when any input is fully lost, after leaving
inspectable artifacts for diagnosis.

## ONNX policy bundle

The repository contains the small interface fixture
[`cvc5-policy-prototype-v1`](model-bundles/cvc5-policy-prototype-v1). A bundle,
not a bare `.onnx` file, is the deployment unit:

```text
model-bundles/cvc5-policy-prototype-v1/
  model.onnx          deterministic ONNX graph
  manifest.json       tensor, option, action, and version contract
  test-vectors.json   cross-runtime golden batch
  sha256sums.txt      artifact integrity checks
  README.md           consumer instructions
```

Tensor contract:

```text
features:       float32[batch, 157]
action_scores:  float32[batch, 4]
action_id:      int64[batch]
config_values:  int64[batch, 30]
config_mask:    bool[batch, 30]
```

The mask determines whether an option is explicitly set. A numeric zero in
`config_values` has no meaning when the corresponding mask is false. Option
names, defaults, types, and enum encodings come exclusively from the manifest.

### JVM consumption

Infrastructure code should use `Cvc5OnnxPolicy`, which verifies the model
checksum, validates dimensions, executes ONNX Runtime, and decodes the result:

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

The public result is:

```kotlin
data class Cvc5SolverConfiguration(
    val actionId: Int,
    val actionName: String,
    val options: Map<String, String>,
)
```

Invalid schemas, incomplete rows, non-finite values, float32 overflow,
unexpected outputs, unknown encodings, and checksum mismatches are rejected.
The caller owns an explicit, observable fallback; the initial fallback is the
unmodified cvc5 configuration.

See [`docs/onnx-policy-contract.md`](docs/onnx-policy-contract.md) for the full
30-option vocabulary, versioning rules, and limitations.

### Rebuild and validate the fixture

```bash
make onnx-env
make onnx-prototype
make onnx-validate
```

Generation is deterministic. The validator checks JSON Schema conformance,
the pinned cvc5 option inventory, model integrity, tensor shapes, and golden
outputs with Python ONNX Runtime. JVM parity is exercised by the Gradle tests.

## Dataset analysis

The selected SMT-LIB 2025 archives, expected file counts, and checksums are
recorded in
[`configs/datasets/smtlib-2025-ten-archive.tsv`](configs/datasets/smtlib-2025-ten-archive.tsv).
Raw archives and generated feature tables are intentionally not committed.

After producing the three files under `data/processed/`:

```bash
make analysis-env
make notebook
```

The notebook verifies feature-table and schema checksums against the extraction
manifest before analysis. The repository includes the executed notebook and
publication figures for experiment E0004. Results and limitations are
summarized in
[`docs/smtlib-2025-dataset-analysis.md`](docs/smtlib-2025-dataset-analysis.md).

## Developer commands

| Command | Purpose |
|---|---|
| `make test` | Run Kotlin/JVM tests |
| `make check` | Run hygiene checks and a clean JVM test build |
| `make analysis-env` | Create `.venv` and install pinned analysis dependencies |
| `make notebook` | Execute the full feature-distribution notebook in place |
| `make cvc5-options` | Regenerate the pinned cvc5 option inventory |
| `make onnx-env` | Create `.venv` and install pinned ONNX tooling |
| `make onnx-prototype` | Rebuild and validate the deterministic ONNX fixture |
| `make onnx-validate` | Validate the committed ONNX bundle |
| `make smoke` | Run the small cvc5 configuration smoke program |

For changes that affect public contracts, run `make check` and
`make onnx-validate`, update the relevant contract identifier, and record
research-impacting work in the experiment registry. Public contribution and
review rules are in [`CONTRIBUTING.md`](CONTRIBUTING.md); coding agents must
also follow [`AGENTS.md`](AGENTS.md).

## Versioned contracts

Artifacts are connected by four independent identifiers:

| Identifier | Defines |
|---|---|
| `dataset_id` | benchmark selection and family metadata |
| `feature_schema` | ordered model inputs and extraction semantics |
| `action_space` | solver version and allowed configurations |
| `model_id` / bundle ID | preprocessing, estimator, interface, and evidence |

Changing one contract never silently reuses outputs produced for another.
Feature ordering or semantics require a new feature-schema ID. Action order,
option encoding, solver version, tensor semantics, or model weights require a
new bundle identity according to the ONNX contract.

## Repository layout

```text
.github/        CI workflow and pull-request template
configs/        reviewed dataset and experiment inputs
data/           data-placement policy; generated data are ignored
docs/           current architecture, contracts, evidence, and references
experiments/    append-only experiment registry; raw run shards are ignored
model-bundles/  small versioned inference bundles and golden vectors
notebooks/      executed, human-readable analyses
reports/        figures selected for publication
schemas/        machine-readable public artifact schemas
scripts/        deterministic generators, validators, and hygiene checks
src/            Kotlin implementation and tests
```

## Documentation map

- [`PROJECT_PLAN.md`](PROJECT_PLAN.md): the only roadmap and future-work list;
- [`RESEARCH.md`](RESEARCH.md): durable decisions, hypotheses, and evidence;
- [`docs/architecture.md`](docs/architecture.md): implemented component
  boundaries and data flow;
- [`docs/feature-schema.md`](docs/feature-schema.md): exact 157-feature
  contract;
- [`docs/feature-clusters.md`](docs/feature-clusters.md): descriptive feature
  groupings and current profiles;
- [`docs/onnx-policy-contract.md`](docs/onnx-policy-contract.md): model-bundle
  and 30-option decoding contract;
- [`docs/experiment-protocol.md`](docs/experiment-protocol.md): mandatory
  methodology for claim-bearing runs;
- [`docs/smtlib-2025-dataset-analysis.md`](docs/smtlib-2025-dataset-analysis.md):
  E0004 results;
- [`docs/references.md`](docs/references.md): research provenance and external
  sources;
- [`experiments/registry.csv`](experiments/registry.csv): machine-readable run
  status and report index.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): development, testing, and review rules;
- [`SECURITY.md`](SECURITY.md): private vulnerability-reporting policy.

## Data and artifact policy

The repository intentionally excludes:

- raw or extracted SMT-LIB corpora;
- generated full feature tables;
- runtime-matrix shards;
- large trained model artifacts;
- local build environments and IDE metadata.

Small reviewable contracts, golden fixtures, schemas, executed notebooks,
selected figures, and the interface ONNX bundle are committed. Hygiene checks
reject known generated-data locations, files larger than 5 MiB, developer-local
absolute paths, a modified Gradle wrapper JAR, and invalid bundle checksums.

## Known limitations

- `ksmt-machsmt-inspired-v1` is inspired by MachSMT but is not binary-compatible
  with a historical MachSMT extractor or model.
- String and regular-expression features are outside the current schema.
- Dataset extraction has deterministic bounded parallelism but no resumable
  shards or parent-enforced process timeout.
- The current dataset has formula features and expected statuses, not runtime
  labels for concrete cvc5 configurations.
- The 30-option vocabulary is a candidate search space, not evidence that all
  combinations are valid or useful.
- The ONNX policy is an interface fixture with hand-authored weights and no
  solver-performance claim.
- Built-in benchmark and configuration commands are smoke tools, not the
  isolated runner required by the experiment protocol.

## Research provenance

The feature design and first specified learned baseline are derived from MachSMT
research, with deliberate changes documented in
[`docs/references.md`](docs/references.md). No MachSMT source files, datasets,
or trained models are vendored, and no affiliation or endorsement is implied.

## License

Licensed under the [Apache License 2.0](LICENSE).
