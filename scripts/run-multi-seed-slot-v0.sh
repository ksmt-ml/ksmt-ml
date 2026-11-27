#!/usr/bin/env bash
set -euo pipefail

if (( $# != 4 )); then
  echo "Usage: $0 WORKSPACE_DIR random|evolution|smac SEED EXPERIMENT_INPUTS_DIR" >&2
  exit 2
fi

workspace=$(realpath "$1")
algorithm=$2
seed=$3
inputs=$(realpath "$4")
if [[ $algorithm != random && $algorithm != evolution && $algorithm != smac ]]; then
  echo "algorithm must be random, evolution, or smac" >&2
  exit 2
fi
if [[ ! $seed =~ ^[0-9]+$ ]]; then
  echo "seed must be a non-negative integer" >&2
  exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ml_root=$(cd "$script_dir/.." && pwd)
python_bin=${PYTHON_BIN:-$ml_root/.venv/bin/python}
campaign="$workspace/design/campaign-v0.json"
corpus="$workspace/design/corpus-v0.json"
calibration_dir="$workspace/calibration/default-noise-validation-balanced-v0"
calibration_schedule="$calibration_dir/default-noise-schedule-v0.json"
calibration_summary="$calibration_dir/default-noise-summary-v0.json"
parameter_space=$(find "$inputs" -maxdepth 1 -type f -name '*parameter-space-v0.json' -print -quit)
tsa_jar="$inputs/tsa-cli.jar"

for required in \
  "$python_bin" \
  "$campaign" \
  "$corpus" \
  "$calibration_schedule" \
  "$calibration_summary" \
  "$parameter_space" \
  "$tsa_jar"; do
  if [[ ! -f $required ]]; then
    echo "required file does not exist: $required" >&2
    exit 2
  fi
done

if ! "$python_bin" -c '
import json,math,sys
campaign=json.load(open(sys.argv[1])); schedule=json.load(open(sys.argv[2])); summary=json.load(open(sys.argv[3]))
valid=(
    schedule.get("schemaVersion") == "cvc5-confirmation-schedule-v0"
    and schedule.get("policy") == "paired-balanced-randomized-interleaved-v0"
    and schedule.get("campaignId") == campaign.get("campaignId")
    and summary.get("schemaVersion") == "cvc5-default-noise-calibration-summary-v0"
    and summary.get("scheduleSha256") == schedule.get("scheduleSha256")
    and summary.get("scoreAggregation") == campaign.get("objective")
    and summary.get("gate", {}).get("passed") is True
    and math.isclose(
        summary.get("targetMinimumRelativeImprovement", -1),
        campaign.get("gate", {}).get("minimumRelativeImprovement", -2),
        rel_tol=0,
        abs_tol=1e-15,
    )
)
raise SystemExit(0 if valid else 1)
' "$campaign" "$calibration_schedule" "$calibration_summary"; then
  echo "balanced default/default calibration gate has not passed; refusing search" >&2
  exit 3
fi

if ! "$python_bin" -c '
import json,sys
c=json.load(open(sys.argv[1])); a=sys.argv[2]; s=int(sys.argv[3])
raise SystemExit(0 if any(x["name"] == a and s in x["seeds"] for x in c["algorithms"]) else 1)
' "$campaign" "$algorithm" "$seed"; then
  echo "$algorithm seed $seed is not declared by the frozen campaign" >&2
  exit 2
fi

development_group_id=${DEVELOPMENT_GROUP_ID:-}
baseline_manifest=$("$python_bin" -c '
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]).resolve(); wanted=sys.argv[2]
c=json.load(open(p)); groups=[g for g in c["groups"] if g["partition"] == "development"]
if wanted: groups=[g for g in groups if g["groupId"] == wanted]
if len(groups) != 1: raise SystemExit("select exactly one development group with DEVELOPMENT_GROUP_ID")
print((p.parent / groups[0]["baselineManifest"]["path"]).resolve())
' "$corpus" "$development_group_id")
baseline_report=$("$python_bin" -c '
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]).resolve(); m=json.load(open(p)); print((p.parent / m["baselineReport"]["fileName"]).resolve())
' "$baseline_manifest")
backend_role=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["workload"]["backendRole"])' "$baseline_manifest")
timeout_penalty=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["evaluationPolicy"]["timeoutPenalty"])' "$baseline_manifest")
wall_seconds=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["budget"]["wallClockSeconds"])' "$campaign")
max_evaluations=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["budget"]["maximumEvaluations"])' "$campaign")
trial_timeout=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["budget"]["perTrialTimeoutSeconds"])' "$campaign")
score_aggregation=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["objective"])' "$campaign")

profile_arguments=()
while IFS= read -r profile; do
  [[ -f $profile ]] || { echo "profile is missing: $profile" >&2; exit 2; }
  profile_arguments+=(--profile "$profile")
done < <("$python_bin" -c 'import json,sys; [print(x["path"]) for x in json.load(open(sys.argv[1]))["workload"]["profiles"]]' "$baseline_manifest")

run_dir="$workspace/runs/$algorithm/seed-$seed"
common=(
  run --strategy "$algorithm"
  --run-dir "$run_dir"
  --cache-dir "$workspace/cache-v0"
  --parameter-space "$parameter_space"
  --baseline "$baseline_report"
  --baseline-manifest "$baseline_manifest"
  --tsa-cli-jar "$tsa_jar"
  --backend-role "$backend_role"
  --repetitions "${SEARCH_REPETITIONS:-1}"
  --warmups "${SEARCH_WARMUPS:-0}"
  --seed "$seed"
  --max-optimizer-trials "$max_evaluations"
  --max-wall-time-seconds "$wall_seconds"
  --per-trial-timeout-seconds "$trial_timeout"
  --timeout-penalty "$timeout_penalty"
  --score-aggregation "$score_aggregation"
  --progress-interval-seconds "${SEARCH_PROGRESS_INTERVAL_SECONDS:-15}"
  "${profile_arguments[@]}"
)

case "$algorithm" in
  random)
    common+=(--random-mutation-probability "${RANDOM_MUTATION_PROBABILITY:-0.05}")
    ;;
  evolution)
    common+=(
      --population-size "${EVOLUTION_POPULATION_SIZE:-8}"
      --elite-fraction "${EVOLUTION_ELITE_FRACTION:-0.25}"
      --evolution-initial-mutation-probability "${EVOLUTION_INITIAL_MUTATION_PROBABILITY:-0.05}"
      --evolution-mutation-probability "${EVOLUTION_MUTATION_PROBABILITY:-0.10}"
    )
    ;;
  smac)
    common+=(--smac-initial-configurations "${SMAC_INITIAL_CONFIGURATIONS:-8}")
    ;;
esac

cd "$ml_root"
PYTHONPATH=src "$python_bin" -m ksmt_ml.tuning "${common[@]}"
