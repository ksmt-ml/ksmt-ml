#!/usr/bin/env bash
set -euo pipefail

if (( $# < 1 || $# > 2 )); then
  echo "Usage: $0 EXPERIMENT_DIR [NEW_CAMPAIGN_ID]" >&2
  exit 2
fi

experiment_dir=$(realpath "$1")
campaign_id=${2:-cvc5-multi-workload-evidence-total-objective-v0}
if [[ ! $campaign_id =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "campaign ID must be lowercase kebab-case" >&2
  exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ml_root=$(cd "$script_dir/.." && pwd)
python_bin=${PYTHON_BIN:-$ml_root/.venv/bin/python}
source_workspace="$experiment_dir/campaign-workspace-v0"
source_campaign="$source_workspace/design/campaign-v0.json"
source_corpus="$source_workspace/design/corpus-v0.json"
target_design="$experiment_dir/design-total-objective-v0"
target_campaign="$target_design/campaign-v0.json"
target_workspace="$experiment_dir/campaign-workspace-total-objective-v0"

for required in "$python_bin" "$source_campaign" "$source_corpus"; do
  if [[ ! -f $required ]]; then
    echo "required source artifact does not exist: $required" >&2
    exit 2
  fi
done

mkdir -p "$target_design"

if [[ ! -e $target_campaign ]]; then
  seed_arguments=()
  while IFS= read -r seed; do
    seed_arguments+=(--seed "$seed")
  done < <("$python_bin" -c '
import json,sys
campaign=json.load(open(sys.argv[1]))
seeds={tuple(item["seeds"]) for item in campaign["algorithms"]}
if len(seeds) != 1: raise SystemExit("source algorithms use different seeds")
for seed in next(iter(seeds)): print(seed)
' "$source_campaign")

  read -r wall maximum timeout repetitions schedule_seed minimum_gain positive_fraction < <(
    "$python_bin" -c '
import json,sys
c=json.load(open(sys.argv[1])); b=c["budget"]; f=c["confirmation"]; g=c["gate"]
print(b["wallClockSeconds"], b["maximumEvaluations"], b["perTrialTimeoutSeconds"], f["repetitions"], f["scheduleSeed"], g["minimumRelativeImprovement"], g["minimumPositiveGroupFraction"])
' "$source_campaign"
  )

  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.experiment_design \
    build-campaign \
    --campaign-id "$campaign_id" \
    --corpus "$source_corpus" \
    --default-candidate-id "$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["defaultCandidateId"])' "$source_campaign")" \
    "${seed_arguments[@]}" \
    --wall-clock-seconds "$wall" \
    --maximum-evaluations "$maximum" \
    --per-trial-timeout-seconds "$timeout" \
    --confirmation-repetitions "$repetitions" \
    --confirmation-seed "$schedule_seed" \
    --minimum-relative-improvement "$minimum_gain" \
    --minimum-positive-group-fraction "$positive_fraction" \
    --objective workload-total-effective-cost-log-speedup-v0 \
    --output "$target_campaign"
else
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.experiment_design \
    validate-campaign --manifest "$target_campaign"
fi

if ! "$python_bin" -c '
import json,sys
c=json.load(open(sys.argv[1]))
raise SystemExit(0 if c.get("campaignId") == sys.argv[2] and c.get("objective") == "workload-total-effective-cost-log-speedup-v0" else 1)
' "$target_campaign" "$campaign_id"; then
  echo "existing target campaign has another ID or objective: $target_campaign" >&2
  exit 2
fi

PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.multi_seed_campaign \
  initialize --campaign "$target_campaign" --output-dir "$target_workspace"

echo "Total-objective campaign workspace ready: $target_workspace"
echo "Old campaign and calibration were not modified."
echo "Next: rerun balanced calibration with CAMPAIGN_WORKSPACE=$target_workspace"
