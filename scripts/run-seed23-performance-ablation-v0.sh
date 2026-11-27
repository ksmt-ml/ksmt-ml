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
parameter_space=$(find "$experiment_dir/inputs" -maxdepth 1 -type f -name '*parameter-space-v0.json' -print -quit)
tsa_cli_jar="$experiment_dir/inputs/tsa-cli.jar"
ablation_root="$experiment_dir/diagnostics/seed23-option-subset-ablation-v0"
correctness_results="$ablation_root/restricted-wallet-correctness-v0/results-v0.json"
baseline_manifest="$experiment_dir/workloads/highload-wallet-test-v0/baseline/default-manifest-v0.json"
performance_root="$ablation_root/highload-wallet-performance-v0"

if [[ -z $parameter_space || ! -f $tsa_cli_jar || ! -f $correctness_results || ! -f $baseline_manifest ]]; then
  echo "seed-23 performance ablation inputs are incomplete under $experiment_dir" >&2
  exit 2
fi

cd "$repo_root"

PYTHONPATH=src "$python_bin" -m ksmt_ml.option_ablation run \
  --manifest "$ablation_root/manifest-v0.json" \
  --eligibility-results "$correctness_results" \
  --baseline-manifest "$baseline_manifest" \
  --parameter-space "$parameter_space" \
  --tsa-cli-jar "$tsa_cli_jar" \
  --output-dir "$performance_root" \
  --repetitions "${ABLATION_REPETITIONS:-3}" \
  --warmups "${ABLATION_WARMUPS:-1}" \
  --matrix-timeout-seconds "${ABLATION_MATRIX_TIMEOUT_SECONDS:-1800}" \
  --trial-timeout-seconds "${ABLATION_TRIAL_TIMEOUT_SECONDS:-300}" \
  --progress-interval-seconds "${ABLATION_PROGRESS_INTERVAL_SECONDS:-15}" \
  --timeout-penalty "${TIMEOUT_PENALTY:-10}"

echo "Performance ablation complete. This is diagnostic evidence, not a new sealed test."
echo "Result: $performance_root/results-v0.json"
