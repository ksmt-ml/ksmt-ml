#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
  echo "Usage: $0 EXPERIMENT_DIR CAMPAIGN_ID" >&2
  exit 2
fi

experiment_dir=$(realpath "$1")
campaign_id=$2
if [[ ! $campaign_id =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "campaign ID must be lowercase kebab-case" >&2
  exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ml_root=$(cd "$script_dir/.." && pwd)
tsa_root=$(cd "$ml_root/../tsa" && pwd)
python_bin=${PYTHON_BIN:-$ml_root/.venv/bin/python}
workloads="$experiment_dir/workloads-v0.tsv"
inputs="$experiment_dir/inputs"
design="$experiment_dir/design"
workspace="$experiment_dir/campaign-workspace-v0"
source_parameter_space=${PARAMETER_SPACE:-$ml_root/configs/cvc5-regular-bv-parameter-space-v0.json}
source_default_candidate=${DEFAULT_CANDIDATE:-$ml_root/configs/cvc5-regular-bv-default-v0.json}
source_tsa_jar=${TSA_CLI_JAR:-$tsa_root/tsa-cli/build/libs/tsa-cli.jar}

for required in "$python_bin" "$workloads" "$source_parameter_space" "$source_default_candidate" "$source_tsa_jar"; do
  if [[ ! -f $required ]]; then
    echo "required file does not exist: $required" >&2
    exit 2
  fi
done

mkdir -p "$inputs" "$design"
freeze_input() {
  local source=$1 destination=$2
  if [[ -e $destination ]]; then
    if ! cmp -s "$source" "$destination"; then
      echo "frozen input differs from current source: $destination" >&2
      exit 2
    fi
  else
    cp "$source" "$destination"
  fi
}

parameter_space="$inputs/$(basename "$source_parameter_space")"
default_candidate="$inputs/$(basename "$source_default_candidate")"
tsa_jar="$inputs/tsa-cli.jar"
freeze_input "$source_parameter_space" "$parameter_space"
freeze_input "$source_default_candidate" "$default_candidate"
freeze_input "$source_tsa_jar" "$tsa_jar"

group_arguments=()
while IFS=$'\t' read -r partition group_id leakage_group_id contract capture_seconds method_count minimum_profiles extra; do
  [[ -z ${partition// } || $partition == \#* ]] && continue
  profile_dir="$experiment_dir/workloads/$group_id/profiles/dev"
  baseline_dir="$experiment_dir/workloads/$group_id/baseline"
  report="$baseline_dir/default-report-v0.json"
  manifest="$baseline_dir/default-manifest-v0.json"
  if [[ ! -d $profile_dir ]] || [[ -z $(find "$profile_dir" -maxdepth 1 -type f -name '*.json' -print -quit) ]]; then
    echo "captured profiles are missing for $group_id" >&2
    exit 2
  fi
  mkdir -p "$baseline_dir"
  if [[ -e $report || -e $manifest ]]; then
    if [[ ! -f $report || ! -f $manifest ]]; then
      echo "partial baseline artifacts for $group_id; do not overwrite them" >&2
      exit 2
    fi
    echo "Keeping completed baseline for $group_id"
  else
    echo "Building default baseline for $partition workload $group_id"
    PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.evaluator baseline \
      --tsa-cli-jar "$tsa_jar" \
      --candidate "$default_candidate" \
      --parameter-space "$parameter_space" \
      --profile-dir "$profile_dir" \
      --repetitions "${BASELINE_REPETITIONS:-1}" \
      --warmups "${BASELINE_WARMUPS:-0}" \
      --matrix-timeout-seconds "${BASELINE_MATRIX_TIMEOUT_SECONDS:-3600}" \
      --trial-timeout-seconds "${BASELINE_TRIAL_TIMEOUT_SECONDS:-600}" \
      --progress-interval-seconds "${BASELINE_PROGRESS_INTERVAL_SECONDS:-15}" \
      --timeout-penalty "${TIMEOUT_PENALTY:-10}" \
      --output "$report" \
      --manifest-output "$manifest"
  fi
  group_arguments+=(--group "$partition" "$group_id" "$leakage_group_id" "$manifest")
done < "$workloads"

corpus="$design/corpus-v0.json"
campaign="$design/campaign-v0.json"
parameter_space_id=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["parameterSpaceId"])' "$parameter_space")
default_candidate_id=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["candidateId"])' "$default_candidate")
solver_version=${CVC5_VERSION:-1.3.4}

if [[ -e $corpus ]]; then
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.experiment_design \
    validate-corpus --manifest "$corpus"
else
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.experiment_design \
    build-corpus \
    --corpus-id "${campaign_id}-corpus-v0" \
    --solver-version "$solver_version" \
    --parameter-space-id "$parameter_space_id" \
    --grouping-key contract-family-and-revision-v0 \
    "${group_arguments[@]}" \
    --output "$corpus"
fi

seed_arguments=()
IFS=',' read -r -a seeds <<< "${CAMPAIGN_SEEDS:-17,23,31}"
for seed in "${seeds[@]}"; do
  if [[ ! $seed =~ ^[0-9]+$ ]]; then
    echo "CAMPAIGN_SEEDS must be comma-separated non-negative integers" >&2
    exit 2
  fi
  seed_arguments+=(--seed "$seed")
done

if [[ -e $campaign ]]; then
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.experiment_design \
    validate-campaign --manifest "$campaign"
else
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.experiment_design \
    build-campaign \
    --campaign-id "$campaign_id" \
    --corpus "$corpus" \
    --default-candidate-id "$default_candidate_id" \
    "${seed_arguments[@]}" \
    --wall-clock-seconds "${SEARCH_WALL_CLOCK_SECONDS:-1800}" \
    --maximum-evaluations "${MAXIMUM_EVALUATIONS:-1000000}" \
    --per-trial-timeout-seconds "${PER_TRIAL_TIMEOUT_SECONDS:-600}" \
    --confirmation-repetitions "${CONFIRMATION_REPETITIONS:-3}" \
    --confirmation-seed "${CONFIRMATION_SCHEDULE_SEED:-20261102}" \
    --minimum-relative-improvement "${MINIMUM_RELATIVE_IMPROVEMENT:-0.05}" \
    --minimum-positive-group-fraction "${MINIMUM_POSITIVE_GROUP_FRACTION:-0.60}" \
    --objective "${CAMPAIGN_OBJECTIVE:-workload-total-effective-cost-log-speedup-v0}" \
    --output "$campaign"
fi

PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.multi_seed_campaign \
  initialize --campaign "$campaign" --output-dir "$workspace"

echo "Campaign workspace ready: $workspace"
echo "STOP and validate all default baselines before noise calibration."
