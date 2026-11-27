#!/usr/bin/env bash
set -euo pipefail

if (( $# != 3 )); then
  echo "Usage: $0 WORKSPACE_DIR PARAMETER_SPACE TSA_CLI_JAR" >&2
  exit 2
fi

workspace=$(realpath "$1")
parameter_space=$(realpath "$2")
tsa_cli_jar=$(realpath "$3")

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
python_bin=${PYTHON_BIN:-python3}
campaign="$workspace/design/campaign-v0.json"
corpus="$workspace/design/corpus-v0.json"
score_aggregation=$(jq -r '.objective' "$campaign")
schedule="$workspace/confirmation/schedule-v0.json"
candidate_index="$workspace/confirmation/candidate-index-v0.json"
confirmation_dir="$workspace/confirmation/run"
observations="$confirmation_dir/observations-v0.json"
summary="$workspace/reports/confirmation-summary-v0.json"

cd "$repo_root"

PYTHONPATH=src "$python_bin" -m ksmt_ml.multi_seed_campaign \
  collect-finalists --workspace "$workspace"
PYTHONPATH=src "$python_bin" -m ksmt_ml.multi_seed_campaign \
  summarize-anytime --workspace "$workspace"
PYTHONPATH=src "$python_bin" -m ksmt_ml.multi_seed_campaign \
  prepare-confirmation --workspace "$workspace"

PYTHONPATH=src "$python_bin" -m ksmt_ml.confirmation_runner \
  --schedule "$schedule" \
  --corpus "$corpus" \
  --parameter-space "$parameter_space" \
  --tsa-cli-jar "$tsa_cli_jar" \
  --candidate-index "$candidate_index" \
  --score-aggregation "$score_aggregation" \
  --output-dir "$confirmation_dir" \
  --warmups "${CONFIRMATION_WARMUPS:-1}" \
  --matrix-timeout-seconds "${CONFIRMATION_MATRIX_TIMEOUT_SECONDS:-1800}" \
  --trial-timeout-seconds "${CONFIRMATION_TRIAL_TIMEOUT_SECONDS:-600}" \
  --progress-interval-seconds "${CONFIRMATION_PROGRESS_INTERVAL_SECONDS:-15}" \
  --timeout-penalty "${TIMEOUT_PENALTY:-10}"

PYTHONPATH=src "$python_bin" -m ksmt_ml.experiment_design \
  summarize-confirmation \
  --schedule "$schedule" \
  --observations "$observations" \
  --bootstrap-seed "${CONFIRMATION_BOOTSTRAP_SEED:-20261102}" \
  --output "$summary"

echo "Multi-seed campaign confirmation complete: $summary"
