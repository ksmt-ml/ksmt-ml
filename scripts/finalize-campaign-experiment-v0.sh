#!/usr/bin/env bash
set -euo pipefail

if (( $# != 1 )); then
  echo "Usage: $0 EXPERIMENT_DIR" >&2
  exit 2
fi

experiment_dir=$(realpath "$1")
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
workspace=${CAMPAIGN_WORKSPACE:-}
if [[ -z $workspace ]]; then
  if [[ -f $experiment_dir/campaign-workspace-total-objective-v0/workspace-v0.json ]]; then
    workspace="$experiment_dir/campaign-workspace-total-objective-v0"
  else
    workspace="$experiment_dir/campaign-workspace-v0"
  fi
fi
workspace=$(realpath "$workspace")
parameter_space=$(find "$experiment_dir/inputs" -maxdepth 1 -type f -name '*parameter-space-v0.json' -print -quit)
tsa_jar="$experiment_dir/inputs/tsa-cli.jar"

if [[ -z $parameter_space || ! -f $tsa_jar || ! -f $workspace/workspace-v0.json ]]; then
  echo "prepared campaign inputs are missing under $experiment_dir" >&2
  exit 2
fi

"$script_dir/finalize-multi-seed-campaign-v0.sh" \
  "$workspace" "$parameter_space" "$tsa_jar"
