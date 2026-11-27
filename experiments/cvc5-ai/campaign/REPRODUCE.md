# Reproduce the cvc5 campaign on this host

Run from `~/Dev/cvc5-ai-pilot/campaign`. The frozen inputs and scripts are
listed in `dataset-v1/frozen.json`. Run paths must be new so prior trial logs and
lock files are preserved. Use the original corpus and lock files for a
measurement replay. Sol sampling, live USVM wall-clock exploration and solver
timings can vary on repeat runs.

```bash
cd ~/Dev/cvc5-ai-pilot/campaign
v1/bootstrap.sh
mkdir -p runs/repeat-1
v1/run.sh smoke runs/repeat-1/smoke
v1/run.sh report runs/repeat-1
```

After reviewing the smoke lock records, run the two full 60-minute waves.

```bash
v1/run.sh full runs/repeat-1/full
v1/run.sh report runs/repeat-1
```

The model runner uses the installed Codex CLI with the existing ChatGPT login.
Its outer mount namespace contains the training bundle, protocol, campaign-local
scratch and Codex runtime. The inner permissions profile exposes training and
hides CLI state from model commands. Each call is bounded. The evaluator is
single-worker per arm. Check `locks.json`, `model-events.jsonl` and the per-call
stderr if an arm is incomplete.

The initial run's held-out seed is revealed under
`runs/campaign-v1/final/unsealed.json` after every full-run choice is locked.
For a repeat from the existing final test, rerun the selected configurations
directly. This keeps the original SMT-LIB sessions and avoids differences caused
by fresh wall-clock-limited symbolic exploration.

```bash
mkdir -p runs/repeat-1/paired
for seed in 18713 36251 59077; do
  case "$seed" in
    18713) core=8 ;;
    36251) core=10 ;;
    59077) core=12 ;;
  esac
  .venv/bin/python -B v1/compare.py rerun \
    runs/campaign-v1/final/test/manifest.json \
    "runs/campaign-v1/final/comparison/locked-$seed.json" \
    "runs/repeat-1/paired/seed-$seed" --seed "$seed" --core "$core"
done
.venv/bin/python -B v1/compare.py report runs/repeat-1/paired
```

To rebuild the tracer, run `v1/usvm/build.sh` with JDK 21 available, the recorded
KSMT dependencies and the pinned Git checkout. Preserve the original classpath
and freeze manifest before a rebuild. A build writes only under this campaign.
The generator, tracer, converter and audit live in `v1/usvm/`. The archived
synthetic pilot under `setup/calibration-pilot` and historical projects are
outside the experiment's scored corpus.

Regenerate the completed report after the main run and optional diagnostics
are present.

```bash
v1/run.sh report runs/campaign-v1
.venv/bin/python -B analysis-v1/enrich_report.py runs/campaign-v1
```

The sealed seed and lock hash are recorded in `final/unsealed.json`. The
per-run `VERIFY.json` independently checks both, then hashes the measurement
logs and verifies the session scores against the oracle.
