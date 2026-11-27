# cvc5 tuning campaign

**Status** · Кампания и закрытая оценка завершены.

Last controller update · 2026-11-21T20:57:43.975369+00:00

## Corpus and evaluator

Fresh generated Java source programs analyzed by USVM at `814a963354209421d36af758edbcec4295b081c4`, with KSMT 0.5.26 and Z3 as the live answer oracle. This estimates performance on unseen programs from the same generator. The cohort is small and consists of bounded integer Java methods.

| Split | Programs | Sessions | Oracle checks | Rejected traces |
|---|---:|---:|---:|---:|
| Train | 15 | 15 | 700 | 3 |
| Validation | 11 | 11 | 794 | 1 |
| Final test | 26 | 26 | 1253 | 1 |

Operations retain their order, including push/pop, assertions, assumption checks, model requests and unsat assumption requests. KSMT context declarations are hoisted. The trace uses the driver’s successful method end and balanced scopes as the completion boundary. The replay checks satisfiability responses against the oracle. It exercises model and core retrieval without comparing the particular model or core. KSMT-side computation on detached models is outside the SMT-LIB replay. Wall-clock cutoffs during live exploration can change a regenerated trace. Frozen SMT-LIB and raw transcripts are the reproducibility artifacts.

Split by generating Java source program, with distinct seeds, exact source and session checks, and pairwise source-operation Jaccard similarity below 0.72. Traces with unknown live oracle answers are excluded. The test seed was committed before tuning. The test sources and sessions are materialized after the full lock file exists. Sol receives only training data.

All scores use fresh real cvc5 1.4.1 processes, incremental mode, solver seed 18473, a 2 GiB address-space limit, fixed CPU affinity and a 1.50 s wall-clock cap per complete session. PAR2 uses elapsed wall time for a fully correct, solved session and 3.00 s for timeouts, unknowns, errors and incorrect sessions. Incorrect answers and errors disqualify a candidate.

## Training calibration

Across 3 default repeats, 39/45 sessions solved and 6 timed out. Median solved latency was 0.153 s. The median within-program relative range was 22.7% over 13 always-solved programs. 0 programs changed outcome across repeats. 58/58 option probes solved the short training session. Pilot results excluded eager bit-blasting with model generation and an incompatible minisat simplification setting from the shared search space.

## Runs

Untuned cvc5 defaults are the reference. Random samples the shared option space independently. Native SMAC 2.4.0 uses its random forest and ask/tell interface, with default plus four Sobol configurations. Sol warm-start SMAC uses default plus four actual Sol proposals before the same SMAC algorithm. Autonomous Sol decides which training configurations to evaluate and can call the same native SMAC. It may supply a Python policy that continues in the training-only sandbox.

Each tuned arm receives one pinned solver worker. Up to six workers run concurrently on separate physical cores. The host can still schedule unrelated work on those cores. Wall-clock accounting begins before each driver starts and includes model CLI startup, search, candidate evaluations and validation. Full budget is 3,600 s per arm and seed, with 180 s reserved for validation. Validation reruns the three best eligible training configurations and default in paired order, with two repeats. The winner minimizes validation PAR2. Validation outcomes are withheld from model turns. Smoke uses 210 s and one validation repeat.

### Smoke

| Seed | Arm | Status | Candidate count | Wall s | Solver CPU s | Sol calls | Sol wall s | Sol input / output tokens |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 91573 | default | ok | 1 | 15.8 | 15.6 | 0 | 0.0 | 0 / 0 |
| 91573 | random | ok | 24 | 171.7 | 169.7 | 0 | 0.0 | 0 / 0 |
| 91573 | smac | ok | 19 | 171.7 | 164.9 | 0 | 0.0 | 0 / 0 |
| 91573 | sol-auto | ok | 13 | 171.7 | 133.6 | 1 | 90.0 | 0 / 0 |
| 91573 | sol-smac | ok | 17 | 180.1 | 152.1 | 1 | 21.0 | 59905 / 425 |


### Full

| Seed | Arm | Status | Candidate count | Wall s | Solver CPU s | Sol calls | Sol wall s | Sol input / output tokens |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 18713 | random | ok | 444 | 3493.6 | 3435.8 | 0 | 0.0 | 0 / 0 |
| 18713 | smac | ok | 392 | 3493.6 | 3295.9 | 0 | 0.0 | 0 / 0 |
| 18713 | sol-auto | ok | 346 | 3494.9 | 3288.8 | 1 | 300.0 | 0 / 0 |
| 18713 | sol-smac | ok | 365 | 3494.9 | 3279.8 | 1 | 23.5 | 53026 / 363 |
| 36251 | random | ok | 426 | 3494.5 | 3431.8 | 0 | 0.0 | 0 / 0 |
| 36251 | smac | ok | 384 | 3494.5 | 3293.2 | 0 | 0.0 | 0 / 0 |
| 36251 | sol-auto | ok | 341 | 3492.7 | 3238.8 | 1 | 244.7 | 635083 / 2997 |
| 36251 | sol-smac | ok | 363 | 3492.7 | 3217.5 | 1 | 93.1 | 78495 / 516 |
| 59077 | random | ok | 432 | 3491.6 | 3435.1 | 0 | 0.0 | 0 / 0 |
| 59077 | smac | ok | 377 | 3493.0 | 3299.5 | 0 | 0.0 | 0 / 0 |
| 59077 | sol-auto | ok | 351 | 3493.3 | 3273.6 | 1 | 292.7 | 554417 / 4186 |
| 59077 | sol-smac | ok | 365 | 3491.6 | 3219.2 | 1 | 89.2 | 68382 / 308 |


## Final held-out outcomes

Means weight Java programs equally. Each seed uses three repeat measurements in random paired arm order inside each session. The final intervals resample programs, and additionally seeds for the crossed interval. A seed count of three limits inference across tuning seeds.

| Arm | Paired seeds | PAR2 s | Ratio to default | 95% program CI | 95% program + seed CI | Solved | Correct / total answers | Timeout / unknown / error / incorrect sessions |
|---|---:|---:|---:|---|---|---:|---:|---:|
| default | 3 | 0.544 | 1.000 | 1.000 to 1.000 | 1.000 to 1.000 | 214 | 10849 / 11277 | 20 / 0 / 0 / 0 |
| random | 3 | 0.540 | 0.991 | 0.923 to 1.048 | 0.894 to 1.065 | 214 | 10630 / 11277 | 20 / 0 / 0 / 0 |
| smac | 3 | 0.501 | 0.921 | 0.828 to 1.018 | 0.781 to 1.037 | 218 | 10745 / 11277 | 16 / 0 / 0 / 0 |
| sol-auto | 3 | 0.540 | 0.993 | 0.948 to 1.035 | 0.899 to 1.082 | 214 | 10668 / 11277 | 20 / 0 / 0 / 0 |
| sol-smac | 3 | 0.555 | 1.019 | 0.956 to 1.080 | 0.916 to 1.118 | 212 | 10665 / 11277 | 22 / 0 / 0 / 0 |

- random seed-level PAR2 ratios · 18713 0.939, 36251 1.015, 59077 1.026.
- smac seed-level PAR2 ratios · 18713 0.928, 36251 0.978, 59077 0.848.
- sol-auto seed-level PAR2 ratios · 18713 0.987, 36251 0.948, 59077 1.050.
- sol-smac seed-level PAR2 ratios · 18713 0.951, 36251 1.021, 59077 1.097.

## Artifacts

- `dataset-v1/frozen.json` records hashes, seeds, hardware assignments and the evaluation contract.
- `logs/` preserves pilot and calibration traces, including the rejected synthetic fallback pilot.
- Each run directory preserves solver-session rows, candidate/config records, checkpoints, native SMAC state, model prompts and transcripts, CPU seconds and model token telemetry.
- ChatGPT login was used for Sol. Total monetary usage and server-side compute are unavailable from the local CLI. No separately metered paid API key was used.
- Primary sources · [cvc5 1.4.1](https://github.com/cvc5/cvc5/releases/tag/cvc5-1.4.1), [SMAC ask/tell](https://automl.github.io/SMAC3/latest/examples/1%20Basics/3_ask_and_tell/), [Codex CLI](https://learn.chatgpt.com/docs/cli/reference).

## Final diagnostics

Corpus and evaluator freeze took 21.1 minutes from the preparation start. The independent final Z3 audit fully confirmed 1253 answers across 26/26 sessions. Audit outcomes and raw rows are under the final run directory.

| Arm | Training sessions solved | Timeout | Unknown | Error | Incorrect | Cut short by budget |
|---|---:|---:|---:|---:|---:|---:|
| random | 9330 | 4546 | 0 | 406 | 0 | 3 |
| smac | 11346 | 3480 | 0 | 179 | 0 | 3 |
| sol-auto | 11820 | 3171 | 0 | 44 | 0 | 4 |
| sol-smac | 11003 | 3444 | 0 | 141 | 0 | 3 |

770 training errors came from the ITE simplification option conflicting with unsat core production. The singleton calibration probe used a short session without this operation. The frozen search space therefore included a choice invalid for part of the corpus. Arms sampled it at different rates, which changes the cost of searching the shared space.

Sol did not emit completion-token telemetry for 1 sol-auto call. Their call counts and wall time are recorded. Token totals in the run table include only calls that emitted usage.

### Direct held-out comparisons

| Arm / reference | PAR2 ratio | 95% program + seed interval | Common seeds |
|---|---:|---|---:|
| smac / random | 0.929 | 0.792 to 1.007 | 3 |
| sol-smac / smac | 1.107 | 0.991 to 1.382 | 3 |
| sol-auto / smac | 1.078 | 0.973 to 1.305 | 3 |

### Training-only offline ablations

The surrogate predicted logged PAR2 for unseen configurations from a withheld random-search seed on the same training programs. The comparison used no final test data.

- ridge_one_hot mean Spearman 0.720, mean best-of-top-ten PAR2 regret 0.029 s.
- random_forest_one_hot mean Spearman 0.728, mean best-of-top-ten PAR2 regret 0.029 s.

- Seed 18713 best initial training PAR2 relative to each run’s default · Sobol 0.778, Sol 0.760. The initial batches took 44.6 s and 73.1 s.
- Seed 36251 best initial training PAR2 relative to each run’s default · Sobol 1.026, Sol 0.988. The initial batches took 61.9 s and 139.8 s.
- Seed 59077 best initial training PAR2 relative to each run’s default · Sobol 0.867, Sol 1.019. The initial batches took 48.2 s and 124.9 s.

`VERIFY.json` cross-checks the lock time, test-seed commitment, test hashes, expected replay counts and per-session PAR2 fields against raw measurements.

### Paired Sol surrogate check (post hoc)
For each of the three random search seeds, both models saw the same 40 measured training configurations and predicted the same 16 hidden eligible candidates. Mean chosen cost above the best in each pool: Sol 0.046 s, random forest 0.220 s, uniform random choice expectation 0.435 s. Mean Spearman correlation: Sol 0.597, forest 0.614. These three offline pools do not establish the performance of a closed-loop optimizer. Scripts, prompts and outputs: surrogate-paired/.
