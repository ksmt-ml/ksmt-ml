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

Random Search and SMAC3 share one adapter from `cvc5-parameter-space-v0` to
ConfigSpace. Install the pinned ConfigSpace 1.2.2 and SMAC3 2.4.1 dependencies
with `make search-env`. The JSON parameter space remains the only catalogue:
the adapter translates domains, `activeWhen`, and forbidden combinations. A
`log-uniform-or-zero` option is represented internally by a generated zero
selector, which is removed when decoding the cvc5 candidate.

The SMAC adapter uses `AlgorithmConfigurationFacade`: its Random Forest and
Expected Improvement choose configurations, while the existing real
per-profile evaluator remains solely responsible for executing cvc5. Workload
profile IDs are SMAC instances and noisy repetitions are SMAC seeds. A SMAC
trial therefore means one isolated `(candidate, profile, seed)` replay, not one
complete candidate over the whole corpus. Until a semantic profile feature
projection is frozen, one-hot identity features let the Random Forest
distinguish development instances without asserting similarity between them;
the adapter also accepts explicit finite profile features later. Successful trials report the measured
normalized cost; known or process timeouts report `TIMEOUT`; wrong answers,
crashes, malformed reports, and infrastructure failures report `CRASHED` with
the original strict status retained in `additional_info`.

The initial design contains cvc5 defaults plus reproducible sparse candidates
from the same `0.05` default-mutation policy used by the Random Search control.
This bootstraps the surrogate near the known-safe default without restricting
later SMAC proposals to a preset catalogue. Multi-fidelity is disabled: no
short profile or partial replay is accepted as a SMAC budget until its ranking
quality has been measured. Durable external/native checkpoint reconciliation
is implemented; an identical command restores the referenced checksummed SMAC
generation and reconciles a pending real solver trial.

Run or resume a bounded Random Search pilot:

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

The same runner provides the evolutionary control required by the experiment
protocol. It is a serial `(mu, lambda)` strategy over typed cvc5 domains: a
complete population is ranked, the best declared fraction becomes the parent
set, and children apply domain-aware mutations. Boolean and categorical values
are never assigned an artificial numeric ordering; integer and real values are
sampled from their declared uniform or log domain. Select it with `--strategy
evolution`; population, elite fraction, initial mutation, and child mutation
are frozen in the manifest and resume checkpoint.

All three search procedures are exposed through one experiment interface:

```bash
PYTHONPATH=src .venv/bin/python -m ksmt_ml.tuning run \
  --strategy random \
  --run-dir experiments/runs/example-random-v0 \
  --parameter-space configs/cvc5-regular-bv-parameter-space-v0.json \
  --baseline experiments/runs/default-v0.json \
  --baseline-manifest experiments/runs/default-manifest-v0.json \
  --tsa-cli-jar ../tsa/tsa-cli/build/libs/tsa-cli.jar \
  --profile-dir ../tsa/solver-profiles/<run> \
  --max-wall-time-seconds 1800 \
  --per-trial-timeout-seconds 600
```

Only `--strategy random|evolution|smac` and strategy-specific hyperparameters
change. `--max-optimizer-trials` is a safety ceiling, not a cross-strategy cost
metric: Random Search and evolution evaluate a complete workload candidate,
while one SMAC trial is one `(candidate, profile, seed)` observation. Fairness
therefore comes from the shared wall-clock budget and holdout policy.

## Development/holdout tuning campaign

Two scripts assemble a claim-oriented campaign without starting heavy work
automatically:

```bash
CAPTURE_TARGET=all CAPTURE_METHOD_COUNT_HINT=10 \
  scripts/capture-func-workload-v0.sh storage-provider-v0 dev \
    ../tsa/tsa-test/src/test/resources/contracts/storage/storage-provider.fc 900
CAPTURE_TARGET=all CAPTURE_METHOD_COUNT_HINT=10 \
  scripts/capture-func-workload-v0.sh storage-provider-v0 holdout \
    ../tsa/tsa-test/src/test/resources/contracts/storage/storage-provider.fc 900

REBUILD_TSA_JAR=1 SEARCH_BUDGET_SECONDS=1800 CONFIRMATION_REPETITIONS=3 \
  scripts/run-tuning-campaign-v0.sh all storage-provider-v0
```

The final capture argument is a wall-clock budget for the selected workload,
not a timeout for every method in the contract. The script splits it between
the declared methods, runs the analysis in a dedicated JVM, and adds a
120-second hard shutdown allowance. By default the JVM heap is limited
to 12288 MiB. On a systemd user session the complete process tree, including
native cvc5 memory, is additionally limited to 18 GiB of RAM and 1 GiB of swap.
The limits can be overridden with `CAPTURE_MAX_HEAP_MB`, `CAPTURE_MEMORY_MAX`,
and `CAPTURE_MEMORY_SWAP_MAX`. A timeout, memory-limit termination, or any
profile that is not closed and capture-complete makes the capture fail instead
of admitting partial data to a campaign. All-method analysis requires an
explicit method-count hint because TSA's native `--timeout` is per method; the
capture script derives the per-method timeout from the total campaign budget.
If a systemd user scope is unavailable, capture fails closed; setting
`CAPTURE_USE_SYSTEMD_SCOPE=0` is an explicit opt-out that leaves native cvc5
memory outside the JVM heap limit.
Low-priority symbolic states are skipped by default. They can be included with
`ANALYZE_LOW_PRIORITY_STATES=1`, while the same hard time and memory limits
remain active.
Full result resolution is used by default so that capture matches normal TSA
analysis. `CAPTURE_RESOLVE_MODE=short` is available only for an explicitly
lightweight smoke workload; such a capture must not be used for a production
performance claim.

For a real all-method workload, declare the target and the method count already
validated by the corresponding TSA integration test. The total budget is split
between those methods. For example, the supported `storage-provider` contract
has ten methods:

```bash
CAPTURE_TARGET=all CAPTURE_METHOD_COUNT_HINT=10 \
  scripts/capture-func-workload-v0.sh storage-provider-v0 dev \
    ../tsa/tsa-test/src/test/resources/contracts/storage/storage-provider.fc 900
```

Capture is rejected if TSA logs an interpreter implementation failure, if any
profile is incomplete, if fewer methods are analyzed than declared, or if no
regular-bv backend profile is produced. A method is not required to create a
solver session: `CAPTURE_METHOD_COUNT_HINT` validates TSA method coverage,
while `CAPTURE_MIN_REGULAR_PROFILES` independently controls the solver-workload
floor. This keeps unsupported contracts such as `elector` out of a
real-workload campaign without rejecting valid solver-free methods.

The stages can instead be invoked separately as `baseline`, `search`, and
`confirm`. Generated data stays under
`experiments/runs/campaigns/<campaign-id>/` and remains ignored by Git. Search
order can be changed with, for example,
`SEARCH_ORDER=smac,evolution,random`; independent campaigns should rotate the
order to expose thermal or temporal machine bias. The final
`confirmation/summary-v0.json` reports eligibility, geometric replay speedup,
per-profile signs, and a deterministic paired bootstrap interval.

This is a solver-replay claim only. A holdout capture of the same contract can
test per-contract tuning, but cannot demonstrate generalization to unseen
contracts. An application-level speedup additionally requires applying the
selected options to a fresh full TSA run and comparing total time, results,
and coverage.

## Durable SMAC3 search

SMAC uses the same strict baseline preflight, real per-profile cvc5 evaluator,
content-addressed cache, subprocess timeout, and signal handling. Its run
directory additionally contains:

```text
manifest.json                    immutable SMAC experiment contract
state.json                       atomic external checkpoint
events.jsonl                     append-only trial trace
trials/<trial-id>/               candidate, report, score, result, or failure
smac/<scenario>/<seed>/          SMAC's current working state
smac-checkpoints/generation-*/   immutable checksummed native generations
```

The external checkpoint never points at files while SMAC is writing them.
After every `ask` and `tell`, all five native SMAC JSON files are parsed, copied
to a new generation, hashed, and completed with a marker written last. On
restart the referenced generation is restored before constructing the facade.
A pending `(candidate, instance, seed)` is then reattached to SMAC's `RUNNING`
trial and a completed `result.json` is reported without repeating cvc5. If the
JVM was killed mid-trial, the same logical trial is retried and its extra active
time remains charged. User idle time between stop and resume is excluded.

Run or resume a bounded SMAC experiment:

```bash
PYTHONPATH=src .venv/bin/python -m ksmt_ml.smac_search run \
  --run-dir experiments/runs/smac-pilot-v0 \
  --cache-dir experiments/cache-v0 \
  --parameter-space configs/cvc5-regular-bv-parameter-space-v0.json \
  --baseline experiments/runs/nominator-clean-6-default-per-profile-v0.json \
  --baseline-manifest experiments/runs/nominator-clean-6-default-manifest-v0.json \
  --tsa-cli-jar ../tsa/tsa-cli/build/libs/tsa-cli.jar \
  --profile-dir experiments/runs/workloads/nominator-clean-6-v0 \
  --seed 17 \
  --repetitions 1 \
  --additional-initial-configurations 8 \
  --max-trials 20 \
  --max-wall-time-seconds 1800 \
  --per-trial-timeout-seconds 300
```

`--max-trials` counts optimizer-visible `(configuration, profile, seed)`
observations, including cache hits, so cached knowledge is not free in a fair
comparison. `actualEvaluationCount` separately records logical cache misses.
An identical command resumes; any changed workload, JAR, parameter space,
baseline, scoring policy, seed, or budget is rejected.

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
