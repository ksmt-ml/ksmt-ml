#!/usr/bin/env bash
set -euo pipefail

if (( $# != 3 )); then
  echo "Usage: $0 SOURCE_CALIBRATION_DIR TARGET_CAMPAIGN OUTPUT_DIR" >&2
  exit 2
fi

source_dir=$(realpath "$1")
campaign=$(realpath "$2")
mkdir -p "$3"
output_dir=$(cd "$3" && pwd)
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ml_root=$(cd "$script_dir/.." && pwd)
python_bin=${PYTHON_BIN:-$ml_root/.venv/bin/python}

aggregation=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["objective"])' "$campaign")
minimum_gain=$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["gate"]["minimumRelativeImprovement"])' "$campaign")

PYTHONPATH="$ml_root/src" "$python_bin" -m ksmt_ml.experiment_design \
  rescore-default-noise \
  --schedule "$source_dir/default-noise-schedule-v0.json" \
  --observations "$source_dir/observations-v0.json" \
  --score-aggregation "$aggregation" \
  --target-minimum-relative-improvement "$minimum_gain" \
  --bootstrap-seed "${CALIBRATION_BOOTSTRAP_SEED:-20261102}" \
  --output-dir "$output_dir"

echo "Exploratory only: this reuses old measurements and cannot unlock search."
echo "Run an independent balanced calibration in the target workspace next."
