#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
  echo "Usage: $0 WORKLOADS_TSV EXPERIMENT_DIR" >&2
  exit 2
fi

workloads=$(realpath "$1")
mkdir -p "$2"
experiment_dir=$(cd "$2" && pwd)
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
frozen_workloads="$experiment_dir/workloads-v0.tsv"

declare -A seen_groups=()
declare -A leakage_partitions=()
declare -A seen_contracts=()
development_count=0
validation_count=0
test_count=0
row_count=0
while IFS=$'\t' read -r partition group_id leakage_group_id contract capture_seconds method_count minimum_profiles extra; do
  [[ -z ${partition// } || $partition == \#* ]] && continue
  if [[ -n ${extra:-} || -z $minimum_profiles ]]; then
    echo "each workload row must contain exactly seven TAB-separated fields" >&2
    exit 2
  fi
  if [[ $partition != development && $partition != validation && $partition != test ]]; then
    echo "invalid partition for $group_id: $partition" >&2
    exit 2
  fi
  if [[ ! $group_id =~ ^[a-z0-9][a-z0-9-]*$ ]] || [[ -n ${seen_groups[$group_id]:-} ]]; then
    echo "group IDs must be unique lowercase kebab-case values: $group_id" >&2
    exit 2
  fi
  if [[ ! $leakage_group_id =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "invalid leakage group ID: $leakage_group_id" >&2
    exit 2
  fi
  if [[ -n ${leakage_partitions[$leakage_group_id]:-} ]]; then
    echo "workload groups must have distinct independent leakage IDs: $leakage_group_id" >&2
    exit 2
  fi
  if [[ ! $capture_seconds =~ ^[0-9]+$ ]] || (( capture_seconds < 60 )); then
    echo "capture seconds must be an integer of at least 60 for $group_id" >&2
    exit 2
  fi
  if [[ ! $method_count =~ ^[0-9]+$ ]] || (( method_count < 1 )); then
    echo "method count must be positive for $group_id" >&2
    exit 2
  fi
  if [[ ! $minimum_profiles =~ ^[0-9]+$ ]] || (( minimum_profiles < 1 )); then
    echo "minimum profile count must be positive for $group_id" >&2
    exit 2
  fi
  contract=$(realpath "$contract")
  if [[ -n ${seen_contracts[$contract]:-} ]]; then
    echo "the same contract file is listed more than once: $contract" >&2
    exit 2
  fi
  seen_contracts[$contract]=1
  leakage_partitions[$leakage_group_id]=$partition
  seen_groups[$group_id]=1
  ((++row_count))
  case "$partition" in
    development) ((++development_count)) ;;
    validation) ((++validation_count)) ;;
    test) ((++test_count)) ;;
  esac
done < "$workloads"

minimum_test_groups=${MINIMUM_TEST_GROUPS:-3}
if [[ ! $minimum_test_groups =~ ^[0-9]+$ ]] || (( minimum_test_groups < 1 )); then
  echo "MINIMUM_TEST_GROUPS must be a positive integer" >&2
  exit 2
fi
if (( row_count < 2 + minimum_test_groups || development_count != 1 || validation_count < 1 || test_count < minimum_test_groups )); then
  echo "corpus must contain exactly one development group, at least one validation group, and at least $minimum_test_groups test groups" >&2
  exit 2
fi

if [[ -e $frozen_workloads ]]; then
  if ! cmp -s "$workloads" "$frozen_workloads"; then
    echo "workload specification changed after the experiment started" >&2
    exit 2
  fi
else
  cp "$workloads" "$frozen_workloads"
fi

while IFS=$'\t' read -r partition group_id leakage_group_id contract capture_seconds method_count minimum_profiles extra; do
  [[ -z ${partition// } || $partition == \#* ]] && continue
  contract=$(realpath "$contract")
  group_root="$experiment_dir/workloads/$group_id"
  profile_dir="$group_root/profiles/dev"
  if [[ -d $profile_dir ]] && [[ -n $(find "$profile_dir" -maxdepth 1 -type f -name '*.json' -print -quit) ]]; then
    echo "Keeping existing immutable capture for $group_id"
  else
    echo "Capturing $partition workload $group_id"
    CAPTURE_CAMPAIGN_DIR="$group_root" \
    CAPTURE_TARGET=all \
    CAPTURE_METHOD_COUNT_HINT="$method_count" \
    CAPTURE_MIN_REGULAR_PROFILES="$minimum_profiles" \
      "$script_dir/capture-func-workload-v0.sh" \
        "$group_id" dev "$contract" "$capture_seconds"
  fi
  incomplete_count=$(jq -s '[.[] | select(.state != "CLOSED" or .producer.captureComplete != true)] | length' "$profile_dir"/*.json)
  regular_bv_count=$(jq -s '[.[] | select(.solver.name == "cvc5" and .solver.attributes["backend-role"] == "regular-bv")] | length' "$profile_dir"/*.json)
  if (( incomplete_count != 0 || regular_bv_count < minimum_profiles )); then
    echo "$group_id capture is incomplete or has too few regular-bv profiles" >&2
    exit 1
  fi
done < "$frozen_workloads"

echo "Captured $row_count workload groups. STOP and validate the profiles before building baselines."
