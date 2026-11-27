#!/usr/bin/env bash
set -euo pipefail

if (( $# != 3 )); then
  echo "Usage: $0 WORKSPACE_DIR random|evolution|smac EXPERIMENT_INPUTS_DIR" >&2
  exit 2
fi

workspace=$(realpath "$1")
algorithm=$2
inputs=$(realpath "$3")
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
campaign="$workspace/design/campaign-v0.json"
python_bin=${PYTHON_BIN:-python3}

while IFS= read -r seed; do
  "$script_dir/run-multi-seed-slot-v0.sh" "$workspace" "$algorithm" "$seed" "$inputs"
done < <("$python_bin" -c '
import json,sys
c=json.load(open(sys.argv[1])); a=sys.argv[2]
matches=[x for x in c["algorithms"] if x["name"] == a]
if len(matches) != 1: raise SystemExit("algorithm is not declared")
[print(seed) for seed in matches[0]["seeds"]]
' "$campaign" "$algorithm")

echo "All declared $algorithm seeds are finished or safely resumed."
