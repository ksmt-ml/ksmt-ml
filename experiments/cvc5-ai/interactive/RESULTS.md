# Interactive Sol tuning of cvc5 1.4.1

**Status** · Fresh held-out evaluation completed and verified.

Interactive Sol's PAR2 was **1.0% lower** than prior Sol-auto on the fresh held-out cohort (ratio 0.990, paired program-and-seed 95% interval 0.940 to 1.037). The interval includes equal performance.

## Protocol

- The original campaign is read-only. Its frozen files are verified against `../campaign/dataset-v1/frozen.json`. The host imports its evaluator and validation procedure using the campaign’s installed Python environment. The model sees the original training bundle and options, a local training broker, public docs, and per-run scratch files.
- Each full run has a real 3,600 s wall limit starting before worker launch. The first 3,420 s can include inference, commands, and real cvc5 measurements. The final 180 s is reserved for paired validation of the top three eligible training configurations and the default if absent. The prior tie-break and two validation repeats are reused. Each worker is pinned to one distinct physical core. Search cores for 18713, 36251 and 59077 are 10, 11 and 12. The historical Sol-auto core for 59077 was 10. Running these three searches concurrently avoids sharing that core and changes host concurrency relative to the original waves.
- Each call can use the full remaining search time. After a model-chosen measurement and analysis cycle, the harness immediately resumes the same thread with actual elapsed time, remaining search time and a measured leaderboard. A turn has no independent fixed-length phase. Sol can use scripts and batches inside its turns. The controller never hands off remaining search time to a generated policy. The model cannot access SMAC, historical results, validation, or test files. There is no SMAC request type or import in the mounted protocol.
- The original 13-option space remains unchanged. The prior campaign recorded 770 ITE simplification errors caused by incompatibility with unsat-core production. Failed, stale, and cut-off trials remain in the raw logs. Complete, correct session wall times are scored directly. Timeout, unknown, error or mismatch is scored as 3.0 s at the original 1.5 s cap. Errors or mismatches disqualify training and validation candidates.
- The independent 128-bit held-out seed is committed in [heldout-commitment.json](heldout-commitment.json). The controller releases it only after all three validation locks. The original USVM/KSMT/Z3 driver then materializes new sources, with a similarity exclusion for the historical pilot, train, validation and old test. Admission checks for earlier source and replay duplicates. The original tracer budget, generator, widths, oracle replay and minimum eligible group count are retained.
- Held-out replay uses the original paired-order algorithm and seeds, three repeats and one pinned worker per search seed on cores 8, 10 and 12. Each repeat shuffles the sessions, then shuffles the four arms within each session. Five thousand bootstrap samples resample whole Java programs, with a second interval also resampling paired search seeds. The primary ratio divides interactive PAR2 by Sol-auto PAR2 on the same fresh data.

## Practice and reproducibility

The earlier bootstrap preparation is preserved in `preflight/` and `preparation-before-clarification.log`. That bootstrap was interrupted before any tuning measurement. The first post-clarification smoke, `runs/smoke-01/`, reached actual measurements but its probe failed because a tool can see its own PID namespace’s procfs. The revised smoke checks denial of private runner state and checks that process roots visible to the command cannot reach a private fixture. It also checks absence of SMAC, validation, test, and prior locks. Probe output, prompts, and raw practice attempts are preserved. The second smoke returned `TRAIN_ONLY_OK`, produced 20 completed candidates and stopped the last turn at its 235 s search cutoff. Its validation lock was written at 258.2 s within the declared 300 s budget. The first turn measured six new candidates. Same-thread continuations measured six and seven more before cutoff. The journal records a shift away from the eight-timeout internal bitblast/CaDiCaL probe, then a move toward extended-rewrite and SAT-backend interactions based on Program003 and Program013. [Smoke gate](runs/interactive-v1/smoke-gate.json) · [Smoke journal](runs/smoke-02/model/work/journal.md) · [Smoke telemetry](preflight/smoke-evidence.json).

```bash
cd ~/Dev/cvc5-ai-pilot/interactive
./run-experiment.sh smoke runs/smoke-repeat
./run-experiment.sh full runs/interactive-v1 runs/smoke-02
python3 -B analysis/report.py runs/interactive-v1
```

The runs use the installed Codex CLI with the existing ChatGPT sign-in. No separately metered API key is supplied. The CLI uses the authentication mount and shell commands cannot access it. A fresh repeat after held-out release requires an independent precommit and new output paths. Tool events, broker latency, session results and journal entries are recorded separately to distinguish the time Sol spent waiting for tools from model-side decision intervals.

To replay a single paired seed after the test release, use a new output path and the run's recorded manifest and locks.

```bash
run=runs/interactive-v1
manifest="$(jq -r .manifest "$run/final/cohort.json")"
../campaign/.venv/bin/python -B engine/evaluate.py worker "$manifest" \
  "$run/final/comparison/locked-18713.json" runs/replay-18713 18713 8 3
```

## Evidence of replanning

- **18713** moved from single switches to broad sampling and interaction probes. It avoided ITE simplification after the error in candidate 15. Adding the BV equality engine to candidate 23's CaDiCaL and extended-rewrite combination produced candidate 96 and moved measured training PAR2 from 0.6640 to 0.6499. Candidate 96 informed subsequent local probes and remained on the validation shortlist. The original validation rule selected default for this seed. [Journal](runs/interactive-v1/seed-18713/model/work/journal.md) · [Candidates](runs/interactive-v1/seed-18713/candidates.jsonl).
- **36251** moved from single-option screening to distance-weighted proposals and crosses aimed at the two persistent timeouts. Candidate 166 reached 83/174 answers in Program003, up from the prior best of 63/174, and scored 0.6454. The next cycle removed individual settings. Removing simplification `none` added a timeout (candidate 181), and removing BV CaDiCaL reduced progress to 38/174 answers on Program003 and 22/42 on Program006 (candidate 205). The next batches returned to targeted combinations. [Journal](runs/interactive-v1/seed-36251/model/work/journal.md) · [Candidates](runs/interactive-v1/seed-36251/candidates.jsonl).
- **59077** tested a follow-up batch around internal bit-blasting and dropped the branch after all 12 new internal configurations timed out on eight or nine of the 15 sessions. It next measured one-option neighbors of candidates 133 and 130. Later it used a training-only model of option interactions to select candidates and returned to sparse configurations when dense proposals performed poorly. [Journal](runs/interactive-v1/seed-59077/model/work/journal.md) · [Candidates](runs/interactive-v1/seed-59077/candidates.jsonl).

## Completed measurements

The replay used 26 programs, 26 sessions, 1531 oracle checks per full corpus traversal, and three paired repeats per search seed. The manifest records 1 rejected trace(s). The independent Z3 audit recorded {"ok": 26}.

| Arm | PAR2 s | Relative to default | Solved | Correct / expected answers | Timeout / unknown / error / incorrect |
|---|---:|---:|---:|---:|---:|
| Default | 0.777 | 1.000 | 200 | 11627 / 13779 | 34 / 0 / 0 / 0 |
| Native SMAC | 0.789 | 1.016 | 197 | 11695 / 13779 | 37 / 0 / 0 / 0 |
| Prior Sol-auto | 0.805 | 1.036 | 197 | 11582 / 13779 | 37 / 0 / 0 / 0 |
| Interactive Sol | 0.797 | 1.026 | 197 | 11571 / 13779 | 37 / 0 / 0 / 0 |

| Interactive Sol vs. | PAR2 ratio | Δ PAR2 s | 95% program interval | 95% paired program-and-seed interval | Ratios by seed (18713 / 36251 / 59077) |
|---|---:|---:|---:|---:|---:|
| Default | 1.026 | +0.020 | 0.973 to 1.088 | 0.936 to 1.179 | 1.010 / 0.952 / 1.128 |
| Native SMAC | 1.010 | +0.008 | 0.974 to 1.057 | 0.965 to 1.071 | 1.004 / 1.008 / 1.017 |
| Prior Sol-auto | 0.990 | -0.008 | 0.967 to 1.008 | 0.940 to 1.037 | 0.977 / 0.967 / 1.027 |

## Interactive search and validation

| Seed | CLI turns / same-thread resumes | Completed candidates / eligible | Command or evaluator wall s | CLI wall outside commands and evaluation s | Solver CPU s total | Lock time s |
|---:|---:|---:|---:|---:|---:|---:|
| 18713 | 20 / 19 | 348 / 347 | 2869.8 | 542.4 | 2826.2 | 3487.2 |
| 36251 | 15 / 14 | 372 / 371 | 3059.0 | 353.3 | 3024.9 | 3486.9 |
| 59077 | 14 / 13 | 376 / 375 | 3050.1 | 362.1 | 2982.3 | 3485.8 |

Completed candidate counts include the initial default and configurations rejected for solver errors. The timing union includes both tool intervals observed in the CLI and broker evaluation intervals, including script runs that outlive a single command result. The remaining CLI time includes model inference, network and CLI overhead. Active decision-making time is unavailable as a separate measurement. The journals and changes in evaluated configurations provide direct evidence of decisions.

- **18713**. First prompt at 7.74 s with 3412.26 s to search. Last at 3207.39 s with 212.61 s to search. 20 prompts and live clock values verified. 102 completed commands, 0 web searches, median first tool dispatch 10.84 s, median real evaluation request 7.87 s. Validation locked candidate 0 at 1.3012 PAR2 with `{}`. [First prompt](runs/interactive-v1/seed-18713/model/turn-000.prompt.txt) · [Journal](runs/interactive-v1/seed-18713/model/work/journal.md) · [Broker responses](runs/interactive-v1/seed-18713/broker.jsonl) · [Train sessions](runs/interactive-v1/seed-18713/train-sessions.jsonl) · [Validation](runs/interactive-v1/seed-18713/validation-sessions.jsonl) · [CLI events](runs/interactive-v1/seed-18713/model-events.jsonl).
- **36251**. First prompt at 7.74 s with 3412.26 s to search. Last at 3414.65 s with 5.35 s to search. 15 prompts and live clock values verified. 90 completed commands, 0 web searches, median first tool dispatch 12.11 s, median real evaluation request 7.82 s. Validation locked candidate 211 at 1.3018 PAR2 with `{"bv-sat-solver": "cadical", "bv-solver": "bitblast", "ext-rew-prep": "use", "minisat-simplification": "clause-elim", "sat-solver": "minisat", "simplification": "none"}`. [First prompt](runs/interactive-v1/seed-36251/model/turn-000.prompt.txt) · [Journal](runs/interactive-v1/seed-36251/model/work/journal.md) · [Broker responses](runs/interactive-v1/seed-36251/broker.jsonl) · [Train sessions](runs/interactive-v1/seed-36251/train-sessions.jsonl) · [Validation](runs/interactive-v1/seed-36251/validation-sessions.jsonl) · [CLI events](runs/interactive-v1/seed-36251/model-events.jsonl).
- **59077**. First prompt at 7.79 s with 3412.21 s to search. Last at 3373.43 s with 46.57 s to search. 14 prompts and live clock values verified. 68 completed commands, 0 web searches, median first tool dispatch 6.04 s, median real evaluation request 7.51 s. Validation locked candidate 133 at 1.2891 PAR2 with `{"bv-abstraction": "on", "bv-gauss-elim": "off", "ite-simp": "off", "simplification": "none"}`. [First prompt](runs/interactive-v1/seed-59077/model/turn-000.prompt.txt) · [Journal](runs/interactive-v1/seed-59077/model/work/journal.md) · [Broker responses](runs/interactive-v1/seed-59077/broker.jsonl) · [Train sessions](runs/interactive-v1/seed-59077/train-sessions.jsonl) · [Validation](runs/interactive-v1/seed-59077/validation-sessions.jsonl) · [CLI events](runs/interactive-v1/seed-59077/model-events.jsonl).

Training session outcomes were `{"error": 3, "solved": 13864, "timeout": 2534}`. 3 errors matched the retained ITE simplification/unsat-core conflict. Every seed completed its search and validated within the declared wall budget. The controller retained the results of any interrupted turns and their unfinished trials.

## Evidence and limitations

- The prior Sol-auto runs had one initial call per seed (300.0, 244.7 and 292.7 s) followed by the generated policy on the original single-worker evaluator. The model calls completed 23, 10 and 20 trials. The scripts completed 322, 330 and 330 trials. All these were direct submissions, with zero SMAC-origin trials. Seed 18713 hit the 300 s model deadline and emitted no final usage. The comparison includes historical CLI behavior, batch strategy, feedback policy and actual trial throughput along with the treatment of model time.
- Runtime measurements share the Linux host with unrelated workloads. The new search wave ran three tuning seeds concurrently, and seed 59077 used a different physical core from its historical run. The fresh replay randomizes arm order within the exact same sessions on each pinned replay core.
- Three tuning seeds and a small generator-specific corpus limit generalization. The 1.50 s cap means a few changed timeouts can move PAR2 substantially. Validation differences among some finalists were only a few milliseconds per session under the frozen two-repeat procedure. The answer audit checks recorded satisfiability answers. It exercises model and unsat-assumption retrieval without comparing returned objects.

[Frozen run plan](runs/interactive-v1/plan.json) · [All locks](runs/interactive-v1/locks.json) · [Post-lock seed release](runs/interactive-v1/final/unsealed.json) · [Trace generation log](runs/interactive-v1/final/materialization.log) · [Replay verification](runs/interactive-v1/final/VERIFY.json) · [Paired bootstrap](runs/interactive-v1/final/comparison/pairwise.json) · [Per-turn timing and feedback telemetry](runs/interactive-v1/interaction-telemetry.json) · [Resource configuration](runs/interactive-v1/resources.json) · [Resource samples](runs/interactive-v1/resource-samples.jsonl).

The raw held-out replays are available for [18713](runs/interactive-v1/final/comparison/seed-18713/sessions.jsonl) · [36251](runs/interactive-v1/final/comparison/seed-36251/sessions.jsonl) · [59077](runs/interactive-v1/final/comparison/seed-59077/sessions.jsonl).
