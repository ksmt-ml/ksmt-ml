# ksmt-ml

`ksmt-ml` is the offline experimentation and training repository for a model
that predicts cvc5 parameters from a representative incremental KSMT workload.
It is not a production JVM dependency. Its deployable result is a versioned
ONNX model bundle consumed by `ksmt-ml-tuner`.

## MVP lifecycle

KSMT defines the only public workload profile: `KSolverProfile`. The same
versioned feature projection must be used while preparing training data and in
the production JVM library.

```text
offline, before deployment

KSolverProfile samples
    -> deterministic feature projection
    -> offline cvc5 replay and configuration evaluation
    -> labelled examples (features, target cvc5 configuration)
    -> model training and sealed evaluation
    -> ONNX export and cross-runtime golden vectors
    -> versioned model bundle

runtime, inside the application

KSolverProfile
    -> ksmt-ml-tuner
    -> one pre-trained ONNX inference
    -> validated cvc5 configuration patch
```

The MVP does not train or refine a model in the application. Runtime inference
does not replay a workload, run an optimizer, or have a tuning-time budget.
How labelled configurations are obtained is part of offline dataset preparation
and is deliberately not a production interface. The v0 evaluator replays complete
TSA backend profiles in isolated JVM processes; it is never used at runtime.

## Repository split

| Repository | Responsibility |
|---|---|
| KSMT | Observation-only `KSolverProfile` and future profiling wrapper |
| `ksmt-ml-tuner` | cvc5 tuner/decision/patch contracts, production feature projection, pre-trained ONNX inference, validation, and fallback |
| `ksmt-ml` | Offline datasets, training, evaluation, benchmarks, and ONNX bundle export |

The application applies a recommended patch while constructing a fresh cvc5
solver for a later workload run. The MVP never reconfigures an active
incremental solver.

## Stable handoff

The stable boundary of this repository is the model bundle rather than its
experimental Python API. A bundle contains the ONNX graph, a manifest, schema
identifiers, supported cvc5 versions, checksums, and golden input/output
vectors. The schemas under `schemas/` define that handoff.

Training implementations may change freely as long as the exported bundle
remains compatible and the evidence gate is satisfied.

## Evidence path

1. Collect representative profiles and target cvc5 configurations.
2. Freeze the profile, feature, parameter-space, dataset, and split versions.
3. Train simple supervised baselines before increasing model complexity.
4. Compare the predictor with default cvc5, best-fixed, and deterministic
   heuristic baselines on grouped holdout workloads.
5. Export ONNX only when predicted configurations preserve correctness and
   improve the agreed workload objective.
6. Verify parity between the training runtime and the JVM ONNX adapter.

TSA is the first workload source because symbolic execution produces related
incremental queries. Its experimental cvc5 profiler records replayable backend
profiles, and the TSA CLI supplies the matching v0 AST replay adapter.

## Offline evaluator

The evaluator has two deliberately separate parts:

- TSA owns exact decoding and replay of its `tsa-solver-profile-json-v0` format;
- `ksmt-ml` owns candidate validation, process isolation, reward calculation,
  and experiment policy.

Build the TSA CLI once:

```bash
cd ../tsa
./gradlew -PuseLocalKsmt=true :tsa-cli:shadowJar
```

Create a strict default baseline. Passing one `--profile` is useful for a smoke
test; use `--profile-dir` for a complete backend run. The command writes both
the replay report and a content-addressed manifest binding the exact profiles,
parameter space, TSA JAR, cvc5 version, and default candidate. It refuses to
publish the manifest when the baseline contains a wrong answer, known-status
timeout, replay failure, recorded workload failure, incompatible solver, or
non-default option.

The v0 command isolates every `(profile, repetition)` in a fresh JVM and then
aggregates the complete matrix. Candidate search uses the same
`per-profile-process-v0` execution mode; older whole-workload baselines are
intentionally incompatible.

```bash
PYTHONPATH=src python3 -m ksmt_ml.evaluator baseline \
  --tsa-cli-jar ../tsa/tsa-cli/build/libs/tsa-cli.jar \
  --candidate configs/cvc5-regular-bv-default-v0.json \
  --parameter-space configs/cvc5-regular-bv-parameter-space-v0.json \
  --profile-dir ../tsa/solver-profiles/<run> \
  --repetitions 1 \
  --progress-interval-seconds 15 \
  --output experiments/runs/default-v0.json \
  --manifest-output experiments/runs/default-manifest-v0.json
```

Во время replay TSA печатает начало и завершение каждого профиля, а для
долгой solver-операции — heartbeat с текущим профилем, фазой, событием,
`checkId`, числом обработанных событий и прошедшим временем. Интервал можно
изменить; значение `0` отключает heartbeat. Вывод дочернего процесса виден по
умолчанию, `--quiet-child-output` возвращает тихий режим.

Score another replay against the cached default result:

```bash
PYTHONPATH=src python3 -m ksmt_ml.evaluator score \
  --baseline experiments/runs/default-v0.json \
  --candidate experiments/runs/candidate-v0.json \
  --output experiments/runs/candidate-score-v0.json
```

The v0 reward is the workload-macro mean of clipped log speedups. It includes
non-check solver operations and checks with known `SAT`/`UNSAT` references.
Recorded `UNKNOWN` checks are excluded from the objective. A timeout or
`UNKNOWN` on a recorded `SAT`/`UNSAT`, a contradictory answer, or a replay
failure makes the candidate ineligible. The timeout penalty remains recorded
for diagnostics but cannot turn a lost known answer into a speedup.

## cvc5 1.3.4 parameter space

TSA can export metadata from the exact cvc5 native library loaded through
KSMT:

```bash
cd ../tsa
java -jar tsa-cli/build/libs/tsa-cli.jar cvc5-option-inventory \
  --output /tmp/cvc5-runtime-inventory-v0.json
```

Build the reviewed inventory and the regular-bv experiment view:

```bash
PYTHONPATH=src python3 -m ksmt_ml.parameter_space build \
  --runtime-inventory /tmp/cvc5-runtime-inventory-v0.json \
  --review docs/cvc5-1.3.4-option-space-review-ru.md \
  --inventory-output configs/cvc5-1.3.4-option-inventory-v0.json \
  --parameter-space-output configs/cvc5-regular-bv-parameter-space-v0.json
```

The v0 inventory classifies all 518 canonical options. The current regular-bv
view exposes 57 Boolean, categorical, integer, or real solver parameters. It
uses typed activation predicates, curated numeric ranges, linear/log sampling,
and emits configuration patches without redundant default values. Ten options
that caused native cvc5 termination during singleton replay are retained in the
inventory with status `unsafe`, not silently forgotten.

Run the resumable real-solver qualification suite:

```bash
PYTHONPATH=src python3 -m ksmt_ml.parameter_space smoke \
  --parameter-space configs/cvc5-regular-bv-parameter-space-v0.json \
  --tsa-cli-jar ../tsa/tsa-cli/build/libs/tsa-cli.jar \
  --profile-dir ../tsa/solver-profiles/<frozen-run> \
  --output-dir experiments/runs/parameter-space-smoke-v0 \
  --random-candidates 20 \
  --seed 29
```

The local one-profile qualification passed 73 singleton/boundary candidates
and 20 combined random candidates with zero replay failures, known-status
timeouts, or wrong answers. This proves the generator and selected values on
that smoke workload; corpus-wide qualification still requires running the same
command over the frozen multi-profile directory.

## File-based Random Search

The first search implementation deliberately has no database. Following the
same separation used by LLVM MLGO, solver execution remains in the instrumented
runner while experiment orchestration consumes and produces plain files. A run
directory contains:

```text
manifest.json          immutable experiment inputs and provenance
events.jsonl           append-only complete event trace
state.json             atomic resume checkpoint
trials/<trial-id>/     candidate, real replay report, score, or failure
  profile-trials/      resumable one-profile/one-repetition reports and scores
```

A separate content-addressed file cache stores only completed real replay
results. Cache keys include the candidate, workload contents, parameter space,
baseline, TSA runner, fidelity, and scoring policy. Generated run and cache
directories are ignored by Git.

Run or resume a bounded pilot:

The future SMAC path and the Random Search control share one adapter from
`cvc5-parameter-space-v0` to ConfigSpace. Install its pinned dependency with
`make search-env`. The JSON parameter space remains the only catalogue: the
adapter translates domains, `activeWhen`, and forbidden combinations. A
`log-uniform-or-zero` option is represented internally by a generated zero
selector, which is removed when decoding the cvc5 candidate.

```bash
PYTHONPATH=src python3 -m ksmt_ml.search run \
  --run-dir experiments/runs/random-pilot-v0 \
  --cache-dir experiments/cache-v0 \
  --parameter-space configs/cvc5-regular-bv-parameter-space-v0.json \
  --baseline experiments/runs/default-v0.json \
  --baseline-manifest experiments/runs/default-manifest-v0.json \
  --tsa-cli-jar ../tsa/tsa-cli/build/libs/tsa-cli.jar \
  --profile-dir ../tsa/solver-profiles/<run> \
  --seed 17 \
  --random-mutation-probability 0.05 \
  --max-evaluations 20 \
  --max-wall-time-seconds 3600 \
  --per-trial-timeout-seconds 600
```

Re-running the identical command resumes the checkpoint. A changed seed,
mutation probability, budget, workload, evaluator, or scoring policy is
rejected instead of silently mixing incomparable trials. Random Search uses a
declared default-centred prior: independently for every option it keeps the
cvc5 default with probability `0.95` and samples a non-default value with
probability `0.05`. This preserves non-zero support for the complete reviewed
space while avoiding uninformative proposals that simultaneously change about
half of all 57 options. Every cache miss launches the actual TSA JVM and the
cvc5 backend; there is no synthetic evaluator or recorded-time predictor.
The implementation records active search time, so time between an interruption
and resume does not consume the search budget. Restarting an interrupted
pending trial also does not consume a second logical evaluation slot; any
additional process time is still charged to the wall-clock budget.

Before creating or resuming a run, search performs a strict preflight: it
rehashes the baseline report, parameter space, TSA JAR, and every selected
profile; validates the complete replay report; confirms the exact default
candidate and compatible cvc5 version; and requires a clean zero self-score.
Candidate process timeouts, native crashes, JVM failures, invalid reports,
infrastructure failures, and replay correctness failures are stored as distinct
trial outcomes. SIGINT and SIGTERM terminate the complete JVM process group and
checkpoint the pending trial. A five-second durable heartbeat bounds lost
wall-clock accounting after SIGKILL or power loss. A candidate is stopped after
the first completed profile that already proves a wrong answer, known-status
timeout, or replay failure; later profiles cannot make such a candidate
eligible again. Cache hits are accepted only after checking the identity and
SHA-256 of every cached artifact.

The storage choice is informed by the official
[LLVM MLGO design](https://www.llvm.org/docs/MLGO.html) and its
[streaming training logger](https://llvm.org/docs/doxygen/TrainingLogger_8h_source.html):
the compiler/solver side stays small and dependency-free, while orchestration
and durable artifacts live outside it.

## Future extension

An online evaluator, online configuration search, or an RL policy may be added
after the pre-trained inference MVP works and has a measurable baseline. Those
components will require separate contracts and are not implied by the current
interfaces.

## Рабочие документы

- [План per-instance тюнинга](docs/per-instance-tuning-plan-ru.md);
- [полная ревизия 518 опций cvc5 1.3.4 и проект пространства признаков](docs/cvc5-1.3.4-option-space-review-ru.md);
- [экспериментальный протокол](docs/experiment-protocol.md).

## Current state

| Area | Status |
|---|---|
| KSMT contracts | Profile contracts and recording profiler implemented locally |
| TSA workload adapter | cvc5 profiling and v0 replay implemented locally |
| offline evaluator | v0 isolated replay and reward implemented |
| search orchestration | v0 serial ask/tell loop, Random Search, file cache, trace, and resume implemented |
| `ksmt-ml-tuner` | Interface-only inference library skeleton |
| labelled cvc5 dataset | Not collected |
| trained model | Not implemented |
| production model bundle | Not available |

No tuning results or performance claims exist yet.

## Development

Requirements are Python 3.12 and GNU Make.

```bash
make check
make analysis-env
make onnx-env
```

The authoritative roadmap is [`PROJECT_PLAN.md`](PROJECT_PLAN.md), evaluation
rules are in [`docs/experiment-protocol.md`](docs/experiment-protocol.md), and
the Russian-language per-instance tuning rationale and literature mapping are
in [`docs/per-instance-tuning-plan-ru.md`](docs/per-instance-tuning-plan-ru.md).

## Data policy

Do not commit raw formulas, application traces, generated datasets, experiment
history, or large models. Small synthetic fixtures, schemas, final aggregate
reports, and golden ONNX vectors may be reviewed in Git.
