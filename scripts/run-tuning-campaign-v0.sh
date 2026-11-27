#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <baseline|search|confirm|all> <campaign-id>" >&2
  exit 2
fi

action=$1
campaign_id=$2
if [[ $action != baseline && $action != search && $action != confirm && $action != all ]]; then
  echo "unknown action: $action" >&2
  exit 2
fi
if [[ ! $campaign_id =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "campaign-id must contain only lowercase letters, digits, and hyphens" >&2
  exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ml_root=$(cd "$script_dir/.." && pwd)
tsa_root=$(cd "$ml_root/../tsa" && pwd)
campaign_dir="$ml_root/experiments/runs/campaigns/$campaign_id"
dev_profiles="$campaign_dir/profiles/dev"
holdout_profiles="$campaign_dir/profiles/holdout"
parameter_space="$ml_root/configs/cvc5-regular-bv-parameter-space-v0.json"
default_candidate="$ml_root/configs/cvc5-regular-bv-default-v0.json"
tsa_jar="$tsa_root/tsa-cli/build/libs/tsa-cli.jar"
python_bin=${PYTHON_BIN:-$ml_root/.venv/bin/python}
cache_dir="$campaign_dir/cache-v0"

search_seconds=${SEARCH_BUDGET_SECONDS:-1800}
per_trial_seconds=${PER_TRIAL_TIMEOUT_SECONDS:-600}
search_seed=${SEARCH_SEED:-17}
max_evaluations=${MAX_EVALUATIONS:-1000000}
confirmation_repetitions=${CONFIRMATION_REPETITIONS:-3}
population_size=${EVOLUTION_POPULATION_SIZE:-4}

require_file() {
  if [[ ! -f $1 ]]; then
    echo "required file does not exist: $1" >&2
    exit 2
  fi
}

require_profiles() {
  if [[ ! -d $1 ]] || [[ -z $(find "$1" -maxdepth 1 -type f -name '*.json' -print -quit) ]]; then
    echo "profile directory is empty: $1" >&2
    exit 2
  fi
}

run_dev_baseline() {
  require_profiles "$dev_profiles"
  mkdir -p "$campaign_dir/baseline"
  if [[ -f $campaign_dir/baseline/dev-default-v0.json && -f $campaign_dir/baseline/dev-default-manifest-v0.json ]]; then
    echo "Development baseline already exists; keeping the frozen artifacts"
    return
  fi
  echo "Building development baseline"
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.evaluator baseline \
    --tsa-cli-jar "$tsa_jar" \
    --candidate "$default_candidate" \
    --parameter-space "$parameter_space" \
    --profile-dir "$dev_profiles" \
    --repetitions "${DEV_BASELINE_REPETITIONS:-1}" \
    --progress-interval-seconds 15 \
    --output "$campaign_dir/baseline/dev-default-v0.json" \
    --manifest-output "$campaign_dir/baseline/dev-default-manifest-v0.json"

}

ensure_holdout_baseline() {
  require_profiles "$holdout_profiles"
  mkdir -p "$campaign_dir/baseline"
  if [[ -f $campaign_dir/baseline/holdout-default-v0.json && -f $campaign_dir/baseline/holdout-default-manifest-v0.json ]]; then
    echo "Holdout baseline already exists; keeping the frozen artifacts"
    return
  fi
  echo "Opening the sealed holdout and building its default baseline"
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.evaluator baseline \
    --tsa-cli-jar "$tsa_jar" \
    --candidate "$default_candidate" \
    --parameter-space "$parameter_space" \
    --profile-dir "$holdout_profiles" \
    --repetitions "$confirmation_repetitions" \
    --progress-interval-seconds 15 \
    --output "$campaign_dir/baseline/holdout-default-v0.json" \
    --manifest-output "$campaign_dir/baseline/holdout-default-manifest-v0.json"
}

run_evolution() {
  echo "Running domain-aware evolutionary search for $search_seconds seconds"
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.tuning run \
    --strategy evolution \
    --run-dir "$campaign_dir/search/evolution-seed${search_seed}-v0" \
    --cache-dir "$cache_dir" \
    --parameter-space "$parameter_space" \
    --baseline "$campaign_dir/baseline/dev-default-v0.json" \
    --baseline-manifest "$campaign_dir/baseline/dev-default-manifest-v0.json" \
    --tsa-cli-jar "$tsa_jar" \
    --profile-dir "$dev_profiles" \
    --seed "$search_seed" \
    --population-size "$population_size" \
    --elite-fraction "${EVOLUTION_ELITE_FRACTION:-0.25}" \
    --evolution-initial-mutation-probability "${EVOLUTION_INITIAL_MUTATION_PROBABILITY:-0.05}" \
    --evolution-mutation-probability "${EVOLUTION_MUTATION_PROBABILITY:-0.10}" \
    --max-optimizer-trials "$max_evaluations" \
    --max-wall-time-seconds "$search_seconds" \
    --per-trial-timeout-seconds "$per_trial_seconds" \
    --progress-interval-seconds 15
}

run_random() {
  echo "Running independent Random Search for $search_seconds seconds"
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.tuning run \
    --strategy random \
    --run-dir "$campaign_dir/search/random-seed${search_seed}-v0" \
    --cache-dir "$cache_dir" \
    --parameter-space "$parameter_space" \
    --baseline "$campaign_dir/baseline/dev-default-v0.json" \
    --baseline-manifest "$campaign_dir/baseline/dev-default-manifest-v0.json" \
    --tsa-cli-jar "$tsa_jar" \
    --profile-dir "$dev_profiles" \
    --seed "$search_seed" \
    --random-mutation-probability "${RANDOM_MUTATION_PROBABILITY:-0.05}" \
    --max-optimizer-trials "$max_evaluations" \
    --max-wall-time-seconds "$search_seconds" \
    --per-trial-timeout-seconds "$per_trial_seconds" \
    --progress-interval-seconds 15
}

run_smac() {
  echo "Running SMAC3 for $search_seconds seconds"
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.tuning run \
    --strategy smac \
    --run-dir "$campaign_dir/search/smac-seed${search_seed}-v0" \
    --cache-dir "$cache_dir" \
    --parameter-space "$parameter_space" \
    --baseline "$campaign_dir/baseline/dev-default-v0.json" \
    --baseline-manifest "$campaign_dir/baseline/dev-default-manifest-v0.json" \
    --tsa-cli-jar "$tsa_jar" \
    --profile-dir "$dev_profiles" \
    --seed "$search_seed" \
    --repetitions 1 \
    --smac-initial-configurations "${SMAC_INITIAL_CONFIGURATIONS:-8}" \
    --max-optimizer-trials "$max_evaluations" \
    --max-wall-time-seconds "$search_seconds" \
    --per-trial-timeout-seconds "$per_trial_seconds" \
    --progress-interval-seconds 15
}

run_searches() {
  require_file "$campaign_dir/baseline/dev-default-v0.json"
  require_file "$campaign_dir/baseline/dev-default-manifest-v0.json"
  mkdir -p "$campaign_dir/search"
  echo "Checking that the ES budget can cover at least two baseline-sized populations"
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.campaign budget-advice \
    --baseline "$campaign_dir/baseline/dev-default-v0.json" \
    --search-budget-seconds "$search_seconds" \
    --per-trial-timeout-seconds "$per_trial_seconds" \
    --population-size "$population_size" \
    --minimum-generations 2
  order=${SEARCH_ORDER:-random,evolution,smac}
  IFS=',' read -r -a strategies <<< "$order"
  if [[ ${#strategies[@]} -ne 3 ]]; then
    echo "SEARCH_ORDER must list random,evolution,smac exactly once" >&2
    exit 2
  fi
  seen_order=" ${strategies[*]} "
  for required in random evolution smac; do
    if [[ $seen_order != *" $required "* ]]; then
      echo "SEARCH_ORDER must list random,evolution,smac exactly once" >&2
      exit 2
    fi
  done
  for strategy in "${strategies[@]}"; do
    case "$strategy" in
      random) run_random ;;
      evolution) run_evolution ;;
      smac) run_smac ;;
      *) echo "unknown strategy in SEARCH_ORDER: $strategy" >&2; exit 2 ;;
    esac
  done
}

confirm_finalists() {
  require_profiles "$holdout_profiles"
  random_run="$campaign_dir/search/random-seed${search_seed}-v0"
  evolution_run="$campaign_dir/search/evolution-seed${search_seed}-v0"
  smac_run="$campaign_dir/search/smac-seed${search_seed}-v0"
  finalists="$campaign_dir/finalists"
  confirmation="$campaign_dir/confirmation"
  mkdir -p "$finalists" "$confirmation"
  ensure_holdout_baseline
  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.campaign extract-finalists \
    --random-run "$random_run" \
    --evolution-run "$evolution_run" \
    --smac-run "$smac_run" \
    --output-dir "$finalists"

  for strategy in random evolution smac; do
    echo "Confirming $strategy incumbent on sealed holdout"
    PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.evaluator run \
      --tsa-cli-jar "$tsa_jar" \
      --candidate "$finalists/${strategy}-candidate-v0.json" \
      --parameter-space "$parameter_space" \
      --profile-dir "$holdout_profiles" \
      --repetitions "$confirmation_repetitions" \
      --progress-interval-seconds 15 \
      --output "$confirmation/${strategy}-report-v0.json"
    PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.evaluator score \
      --baseline "$campaign_dir/baseline/holdout-default-v0.json" \
      --candidate "$confirmation/${strategy}-report-v0.json" \
      --output "$confirmation/${strategy}-score-v0.json"
  done

  PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.campaign summarize \
    --score "$confirmation/random-score-v0.json" \
    --score "$confirmation/evolution-score-v0.json" \
    --score "$confirmation/smac-score-v0.json" \
    --minimum-relative-improvement "${MINIMUM_HOLDOUT_IMPROVEMENT:-0.05}" \
    --minimum-profile-count "${MINIMUM_HOLDOUT_PROFILE_COUNT:-5}" \
    --minimum-positive-profile-fraction "${MINIMUM_POSITIVE_PROFILE_FRACTION:-0.60}" \
    --output "$confirmation/summary-v0.json"
}

require_file "$python_bin"
if [[ ${REBUILD_TSA_JAR:-0} == 1 ]]; then
  echo "Rebuilding the TSA CLI used by every replay"
  (
    cd "$tsa_root"
    APPIMAGE_EXTRACT_AND_RUN=1 ./gradlew \
      --console=plain \
      -PuseLocalTuningContracts=true \
      :tsa-cli:shadowJar
  )
fi
require_file "$tsa_jar"
require_file "$parameter_space"
require_file "$default_candidate"
cd "$ml_root"

case "$action" in
  baseline) run_dev_baseline ;;
  search) run_searches ;;
  confirm) confirm_finalists ;;
  all)
    run_dev_baseline
    run_searches
    confirm_finalists
    ;;
esac
