#!/usr/bin/env bash
set -euo pipefail

if (( $# != 5 )); then
  echo "Usage: $0 CORPUS CAMPAIGN PARAMETER_SPACE TSA_CLI_JAR OUTPUT_DIR" >&2
  exit 2
fi

corpus=$(realpath "$1")
campaign=$(realpath "$2")
parameter_space=$(realpath "$3")
tsa_cli_jar=$(realpath "$4")
mkdir -p "$5"
output_dir=$(cd "$5" && pwd)

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
python_bin=${PYTHON_BIN:-python3}
schedule="$output_dir/default-noise-schedule-v0.json"
observations="$output_dir/observations-v0.json"
summary="$output_dir/default-noise-summary-v0.json"
calibration_repetitions=${CALIBRATION_REPETITIONS:-12}
score_aggregation=$(jq -r '.objective' "$campaign")

cd "$repo_root"

PYTHONPATH=src "$python_bin" -m ksmt_ml.experiment_design validate-corpus \
  --manifest "$corpus"
PYTHONPATH=src "$python_bin" -m ksmt_ml.experiment_design validate-campaign \
  --manifest "$campaign"

if [[ -e $schedule ]]; then
  PYTHONPATH=src "$python_bin" -m ksmt_ml.experiment_design \
    validate-confirmation-schedule --schedule "$schedule"
else
  PYTHONPATH=src "$python_bin" -m ksmt_ml.experiment_design \
    build-confirmation-schedule \
    --campaign "$campaign" \
    --partition validation \
    --candidate default-remeasurement-v0 \
    --default-control default-remeasurement-v0 \
    --repetitions "$calibration_repetitions" \
    --balanced-role-order \
    --output "$schedule"
fi

if [[ $(jq -r '.policy' "$schedule") != paired-balanced-randomized-interleaved-v0 ]]; then
  echo "refusing unbalanced default/default calibration schedule: $schedule" >&2
  exit 2
fi
if [[ $(jq -r '.repetitions' "$schedule") != "$calibration_repetitions" ]]; then
  echo "existing calibration schedule uses another repetition count: $schedule" >&2
  exit 2
fi

PYTHONPATH=src "$python_bin" -m ksmt_ml.confirmation_runner \
  --schedule "$schedule" \
  --corpus "$corpus" \
  --parameter-space "$parameter_space" \
  --tsa-cli-jar "$tsa_cli_jar" \
  --output-dir "$output_dir" \
  --expected-partition validation \
  --score-aggregation "$score_aggregation" \
  --warmups "${CALIBRATION_WARMUPS:-1}" \
  --matrix-timeout-seconds "${CALIBRATION_MATRIX_TIMEOUT_SECONDS:-1800}" \
  --trial-timeout-seconds "${CALIBRATION_TRIAL_TIMEOUT_SECONDS:-600}" \
  --progress-interval-seconds "${CALIBRATION_PROGRESS_INTERVAL_SECONDS:-15}" \
  --timeout-penalty "${TIMEOUT_PENALTY:-10}"

PYTHONPATH=src "$python_bin" -m ksmt_ml.experiment_design \
  summarize-default-noise \
  --campaign "$campaign" \
  --schedule "$schedule" \
  --observations "$observations" \
  --bootstrap-seed "${CALIBRATION_BOOTSTRAP_SEED:-20261102}" \
  --output "$summary"

if ! jq -e '.gate.passed == true' "$summary" >/dev/null; then
  echo "Default/default calibration gate FAILED; do not start search." >&2
  jq '{pairedRepetitionCount, correctness, metrics, gate}' "$summary" >&2
  exit 3
fi

echo "Default/default calibration gate passed: $summary"
