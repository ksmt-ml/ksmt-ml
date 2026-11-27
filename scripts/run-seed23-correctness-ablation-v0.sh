#!/usr/bin/env bash
set -euo pipefail

if (( $# != 1 )); then
  echo "Usage: $0 EXPERIMENT_DIR" >&2
  exit 2
fi

experiment_dir=$(realpath "$1")
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
python_bin=${PYTHON_BIN:-python3}
workspace=${CAMPAIGN_WORKSPACE:-$experiment_dir/campaign-workspace-total-objective-v0}
workspace=$(realpath "$workspace")
parameter_space=$(find "$experiment_dir/inputs" -maxdepth 1 -type f -name '*parameter-space-v0.json' -print -quit)
tsa_cli_jar="$experiment_dir/inputs/tsa-cli.jar"
source_candidate="$workspace/confirmation/finalists/random-seed23-candidate-v0.json"
baseline_manifest="$experiment_dir/workloads/restricted-wallet-test-v0/baseline/default-manifest-v0.json"
ablation_root="$experiment_dir/diagnostics/seed23-option-subset-ablation-v0"
correctness_root="$ablation_root/restricted-wallet-correctness-v0"

if [[ -z $parameter_space || ! -f $tsa_cli_jar || ! -f $source_candidate || ! -f $baseline_manifest ]]; then
  echo "seed-23 ablation inputs are incomplete under $experiment_dir" >&2
  exit 2
fi

cd "$repo_root"

PYTHONPATH=src "$python_bin" -m ksmt_ml.option_ablation prepare \
  --source-candidate "$source_candidate" \
  --parameter-space "$parameter_space" \
  --output-dir "$ablation_root"

PYTHONPATH=src "$python_bin" -m ksmt_ml.option_ablation run \
  --manifest "$ablation_root/manifest-v0.json" \
  --baseline-manifest "$baseline_manifest" \
  --parameter-space "$parameter_space" \
  --tsa-cli-jar "$tsa_cli_jar" \
  --output-dir "$correctness_root" \
  --repetitions "${ABLATION_REPETITIONS:-1}" \
  --warmups "${ABLATION_WARMUPS:-0}" \
  --matrix-timeout-seconds "${ABLATION_MATRIX_TIMEOUT_SECONDS:-900}" \
  --trial-timeout-seconds "${ABLATION_TRIAL_TIMEOUT_SECONDS:-120}" \
  --progress-interval-seconds "${ABLATION_PROGRESS_INTERVAL_SECONDS:-15}" \
  --timeout-penalty "${TIMEOUT_PENALTY:-10}"

echo "STOP and review correctness subsets before performance ablation."
echo "Result: $correctness_root/results-v0.json"
