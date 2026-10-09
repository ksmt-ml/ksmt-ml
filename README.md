# ksmt-ml

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

`ksmt-ml` is experimental infrastructure for feature extraction, dataset
analysis, and future automatic SMT solver configuration in
[KSMT](https://github.com/UnitTestBot/ksmt).

The current milestone is intentionally narrower than a complete solver tuner:

```text
SMT-LIB benchmarks
    -> versioned 157-position feature vectors
    -> deterministic feature table
    -> reproducible distribution and data-quality analysis
```

Runtime collection, solver-configuration selection, model training, ONNX
export, and KSMT runtime integration are planned but are not implemented yet.

## Current status

- `ksmt-machsmt-inspired-v1` defines the frozen 157-position KSMT-native
  schema: 145 unique grammar counts, 11 structural values, and source size.
- KSMT AST traversal supplies structural features.
- A lightweight SMT-LIB scanner preserves commands, declarations, source-level
  sorts, and other information that disappears after assertion parsing.
- `QF_ALIA_BASELINE` and `QF_AUFLIA_BASELINE` provide compact logic-aware
  starting profiles.
- The batch extractor emits a deterministic CSV or CSV.GZ table plus a schema
  and a checksummed completion manifest.
- `cvc5-options.txt` is a generated cvc5 1.3.4 option inventory for research
  and action-space design; it is not itself a supported configuration set.
- An executed notebook and generated figures describe all 64,432 selected
  SMT-LIB 2025 benchmarks. The committed analysis is regenerated whenever the
  frozen schema or reviewed cohort changes.
- Seven golden SMT-LIB fixtures and the deterministic batch path are covered by
  tests and CI.

The profile is inspired by the published MachSMT feature design and is
deliberately not a drop-in reproduction of the historical
implementation. See [Feature schema](docs/feature-schema.md),
[Feature extractor roadmap](docs/feature-extractor-roadmap.md), and
[References and provenance](docs/references.md).

## Prerequisites

- JDK 17;
- Python 3.12 or another version compatible with
  [`requirements-analysis.txt`](requirements-analysis.txt);
- GNU Make is optional and only provides command aliases.

The repository includes its own Gradle Wrapper and uses published KSMT 0.6.7
artifacts by default. A sibling KSMT source checkout is optional.

## Start from a fresh clone

```bash
git clone git@github.com:ksmt-ml/ksmt-ml.git
cd ksmt-ml
./gradlew test
```

To test against a local `../ksmt` checkout instead of published artifacts:

```bash
./gradlew -PuseLocalKsmt=true test
```

## Extract features

Show the command-line help:

```bash
./gradlew run --args="--help"
```

Extract one benchmark to standard output:

```bash
./gradlew run --args="--extract-features /path/to/input.smt2"
```

Write the JSON to a file:

```bash
./gradlew run \
  --args="--extract-features /path/to/input.smt2 build/results/input.features.json"
```

Extract a dataset directory. The input root must contain one directory per
SMT-LIB logic, followed by benchmark-family directories:

```bash
./gradlew run --args="--extract-dataset \
  smtlib-2025-ten-archive-v1 \
  /path/to/benchmarks/non-incremental \
  data/processed/smtlib-2025-features.csv.gz \
  configs/datasets/smtlib-2025-ten-archive.tsv \
  YOUR_GIT_COMMIT 4"
```

The output records parse/extraction failures instead of silently dropping
files. If parsing or safe AST traversal fails, syntax features and source size
are kept while structural features 146-156 remain missing. A non-zero count of
fully failed files makes the CLI fail after publishing inspectable artifacts.

## Reproduce the basic analysis

Raw benchmarks and generated feature tables are intentionally excluded from
Git. The selected archives, expected file counts, and checksums are recorded in
[`configs/datasets/smtlib-2025-ten-archive.tsv`](configs/datasets/smtlib-2025-ten-archive.tsv).

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-analysis.txt
./.venv/bin/jupyter lab notebooks/01_smtlib_feature_distributions.ipynb
```

The committed notebook is already executed and verifies the hashes recorded by
the extraction manifest before analysis. Its main conclusions are summarized in
[SMT-LIB 2025 dataset analysis](docs/smtlib-2025-dataset-analysis.md).

## Developer commands

| Command | Purpose |
|---|---|
| `make test` | Run the Kotlin/JVM test suite |
| `make check` | Clean and run all current local quality gates |
| `make analysis-env` | Create `.venv` and install analysis dependencies |
| `make notebook` | Execute the analysis notebook in place |
| `make smoke` | Run the small built-in cvc5 configuration smoke experiment |

## Repository layout

```text
configs/       versioned dataset and future experiment configuration
data/          data policy and ignored local raw/processed datasets
docs/          architecture, protocol, roadmap, and analysis conclusions
experiments/   append-only experiment registry; large run outputs are ignored
notebooks/     human-readable executed analyses
reports/       generated figures selected for publication
scripts/       notebook construction and future reproducibility utilities
src/           Kotlin extractor, dataset CLI, and tests
```

Stable project constraints are recorded in [`AGENTS.md`](AGENTS.md). Research
state and open hypotheses live in [`RESEARCH.md`](RESEARCH.md), the staged
research plan is in [`docs/roadmap.md`](docs/roadmap.md), and individual runs
are indexed in [`experiments/registry.csv`](experiments/registry.csv).

## Known limitations

- Historical MachSMT parity is not claimed; duplicate historical token entries
  were deliberately removed and the exact `v1` contract is documented.
- The batch extractor has bounded parallelism and deterministic output order,
  but not resume support or process isolation with a hard timeout per file.
- The current dataset contains formula features and expected SAT statuses, not
  runtime labels for concrete cvc5 configurations.
- The built-in benchmark commands are smoke tools, not the future isolated
  benchmark runner.

## Roadmap

1. Review the pilot data cohort and freeze family-level splits.
2. Collect an isolated cvc5 configuration runtime matrix.
3. Establish Default/SBS/VBS headroom.
4. Reproduce the MachSMT PCA/AdaBoost EHM baseline.
5. Expand logics, actions, solvers, and model families one axis at a time.

The complete evidence gates and expansion order are in the
[research roadmap](docs/roadmap.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
