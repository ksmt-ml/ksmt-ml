#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 <campaign-id> <dev|holdout> <contract.fc> <wall-clock-budget-seconds>" >&2
  exit 2
fi

campaign_id=$1
role=$2
contract_input=$3
wall_clock_budget_seconds=$4

if [[ ! $campaign_id =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "campaign-id must contain only lowercase letters, digits, and hyphens" >&2
  exit 2
fi
if [[ $role != dev && $role != holdout ]]; then
  echo "role must be dev or holdout" >&2
  exit 2
fi
if [[ ! $wall_clock_budget_seconds =~ ^[0-9]+$ ]] || (( wall_clock_budget_seconds < 60 )); then
  echo "wall-clock-budget-seconds must be an integer of at least 60" >&2
  exit 2
fi

max_heap_mb=${CAPTURE_MAX_HEAP_MB:-12288}
memory_max=${CAPTURE_MEMORY_MAX:-18G}
memory_swap_max=${CAPTURE_MEMORY_SWAP_MAX:-1G}
hard_timeout_grace_seconds=${CAPTURE_HARD_TIMEOUT_GRACE_SECONDS:-120}
use_systemd_scope=${CAPTURE_USE_SYSTEMD_SCOPE:-1}
resolve_mode=${CAPTURE_RESOLVE_MODE:-full}
min_regular_profiles=${CAPTURE_MIN_REGULAR_PROFILES:-1}
if [[ ! $max_heap_mb =~ ^[0-9]+$ ]] || (( max_heap_mb < 512 )); then
  echo "CAPTURE_MAX_HEAP_MB must be an integer of at least 512" >&2
  exit 2
fi
if [[ ! $hard_timeout_grace_seconds =~ ^[0-9]+$ ]] || (( hard_timeout_grace_seconds < 1 )); then
  echo "CAPTURE_HARD_TIMEOUT_GRACE_SECONDS must be a positive integer" >&2
  exit 2
fi
if [[ ! $min_regular_profiles =~ ^[0-9]+$ ]] || (( min_regular_profiles < 1 )); then
  echo "CAPTURE_MIN_REGULAR_PROFILES must be a positive integer" >&2
  exit 2
fi
if [[ $use_systemd_scope != 0 ]] && \
  { ! command -v systemd-run >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1; }; then
  echo "systemd user scopes are unavailable; refusing to run without a native-memory limit" >&2
  echo "set CAPTURE_USE_SYSTEMD_SCOPE=0 only to explicitly accept heap-only isolation" >&2
  exit 2
fi
case "$resolve_mode" in
  full)
    resolve_args=()
    ;;
  short)
    resolve_args=(--short-resolve)
    ;;
  *)
    echo "CAPTURE_RESOLVE_MODE must be full or short" >&2
    exit 2
    ;;
esac

# TSA applies --timeout independently to every method. Split the requested campaign budget by the
# declared target size instead of accidentally granting the complete budget to every method.
capture_target=${CAPTURE_TARGET:-receivers}
case "$capture_target" in
  receivers)
    target_args=(--analyze-receivers)
    target_method_count=2
    ;;
  all)
    target_args=(--analyze-all-methods)
    target_method_count=${CAPTURE_METHOD_COUNT_HINT:-}
    if [[ ! $target_method_count =~ ^[0-9]+$ ]] || (( target_method_count < 1 )); then
      echo "CAPTURE_METHOD_COUNT_HINT must be a positive integer for CAPTURE_TARGET=all" >&2
      exit 2
    fi
    ;;
  *)
    echo "CAPTURE_TARGET must be receivers or all" >&2
    exit 2
    ;;
esac
method_timeout_seconds=${TSA_METHOD_TIMEOUT_SECONDS:-$((wall_clock_budget_seconds / target_method_count))}
if [[ ! $method_timeout_seconds =~ ^[0-9]+$ ]] || (( method_timeout_seconds < 1 )); then
  echo "TSA_METHOD_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
fi
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ml_root=$(cd "$script_dir/.." && pwd)
tsa_root=$(cd "$ml_root/../tsa" && pwd)
contract=$(realpath "$contract_input")
fift_std=${FIFT_STDLIB:-$tsa_root/tsa-test/src/test/resources/fiftstdlib}

if [[ ! -f $contract ]]; then
  echo "contract does not exist: $contract" >&2
  exit 2
fi
if [[ ! -d $fift_std ]]; then
  echo "Fift standard library does not exist: $fift_std" >&2
  exit 2
fi
if [[ $contract == *[[:space:]]* || $fift_std == *[[:space:]]* ]]; then
  echo "TSA Gradle argument transport does not support whitespace in these paths" >&2
  exit 2
fi

campaign_dir=${CAPTURE_CAMPAIGN_DIR:-$ml_root/experiments/runs/campaigns/$campaign_id}
profile_dir="$campaign_dir/profiles/$role"
result_dir="$campaign_dir/capture/$role"
if [[ -e $profile_dir ]] && [[ -n $(find "$profile_dir" -mindepth 1 -print -quit) ]]; then
  echo "profile directory is not empty; choose a new campaign id: $profile_dir" >&2
  exit 2
fi
mkdir -p "$profile_dir" "$result_dir"

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
run_id="${campaign_id}-${role}-${timestamp}"
workload_id="$campaign_id"
sarif="$result_dir/result.sarif"
log="$result_dir/capture.log"
tsa_jar="$tsa_root/tsa-cli/build/libs/tsa-cli.jar"

{
  echo "Capturing $role workload with a $wall_clock_budget_seconds-second wall-clock budget"
  echo "contract=$contract"
  echo "profiles=$profile_dir"
  echo "target=$capture_target, expected-methods=$target_method_count, per-method-timeout=${method_timeout_seconds}s"
  echo "minimum-regular-bv-profiles=$min_regular_profiles"
  echo "resolve-mode=$resolve_mode"
  echo "low-priority-states=${ANALYZE_LOW_PRIORITY_STATES:-0}"
  echo "heap-limit=${max_heap_mb}MiB, process-memory-limit=$memory_max, process-swap-limit=$memory_swap_max"
} | tee "$log"

cd "$tsa_root"
APPIMAGE_EXTRACT_AND_RUN=1 ./gradlew \
  --console=plain \
  -PuseLocalTuningContracts=true \
  :tsa-cli:shadowJar 2>&1 | tee -a "$log"

if [[ ! -f $tsa_jar ]]; then
  echo "TSA CLI jar was not produced: $tsa_jar" >&2
  exit 1
fi

tsa_command=(
  timeout
  --signal=TERM
  "--kill-after=${hard_timeout_grace_seconds}s"
  "${wall_clock_budget_seconds}s"
  java
  "-Xmx${max_heap_mb}m"
  -XX:+ExitOnOutOfMemoryError
  -jar "$tsa_jar"
  func
  -i "$contract"
  --fift-std "$fift_std"
  "${target_args[@]}"
  --timeout "$method_timeout_seconds"
  --solver-timeout "${SOLVER_TIMEOUT_SECONDS:-5}"
  --iteration-limit "${ITERATION_LIMIT:-6}"
  --max-recursion-depth "${MAX_RECURSION_DEPTH:-5}"
  "${resolve_args[@]}"
  --solver-profile-dir "$profile_dir"
  --solver-profile-run-id "$run_id"
  --solver-profile-workload-id "$workload_id"
  --solver-profile-checkpoint-interval "${PROFILE_CHECKPOINT_INTERVAL:-500}"
  --solver-profile-max-events "${PROFILE_MAX_EVENTS:-1000000}"
)
if [[ ${ANALYZE_LOW_PRIORITY_STATES:-0} == 1 ]]; then
  tsa_command+=(--analyze-low-priority-states)
fi
tsa_command+=(-o "$sarif")

capture_command=("${tsa_command[@]}")
if [[ $use_systemd_scope != 0 ]]; then
  capture_command=(
    systemd-run
    --user
    --scope
    --quiet
    --collect
    -p "MemoryMax=$memory_max"
    -p "MemorySwapMax=$memory_swap_max"
    --
    "${tsa_command[@]}"
  )
else
  echo "warning: CAPTURE_USE_SYSTEMD_SCOPE=0; only the JVM heap limit is enforced" | tee -a "$log" >&2
fi

set +e
APPIMAGE_EXTRACT_AND_RUN=1 "${capture_command[@]}" 2>&1 | tee -a "$log"
capture_status=${PIPESTATUS[0]}
set -e

if (( capture_status != 0 )); then
  if (( capture_status == 124 || capture_status == 137 )); then
    echo "capture exceeded its hard wall-clock or memory limit; partial profiles must not be used" >&2
  else
    echo "TSA capture failed with exit code $capture_status" >&2
  fi
  exit "$capture_status"
fi

profile_count=$(find "$profile_dir" -maxdepth 1 -type f -name '*.json' | wc -l)
if (( profile_count == 0 )); then
  echo "TSA completed without producing JSON profiles" >&2
  exit 1
fi
incomplete_count=$(jq -s '[.[] | select(.state != "CLOSED" or .producer.captureComplete != true)] | length' "$profile_dir"/*.json)
if (( incomplete_count != 0 )); then
  echo "TSA produced $incomplete_count incomplete profile(s); refusing to use this capture" >&2
  exit 1
fi
if grep -Fq "Exception is thrown during the interpretation" "$log"; then
  echo "TSA dropped at least one state because of an interpreter implementation failure" >&2
  echo "refusing to admit this capture as a real workload" >&2
  exit 1
fi
regular_bv_count=$(
  jq -s '[.[] | select(.solver.name == "cvc5" and .solver.attributes["backend-role"] == "regular-bv")] | length' \
    "$profile_dir"/*.json
)
if (( regular_bv_count < min_regular_profiles )); then
  echo "expected at least $min_regular_profiles regular-bv profiles, captured $regular_bv_count" >&2
  exit 1
fi
if [[ $capture_target == all ]]; then
  analyzed_method_count=$(grep -Fc "TvmAnalyzerKt - Method " "$log" || true)
  if (( analyzed_method_count < target_method_count )); then
    echo "expected TSA to analyze $target_method_count methods, observed $analyzed_method_count" >&2
    exit 1
  fi
  echo "Observed $analyzed_method_count analyzed methods"
fi
echo "Captured $profile_count profile files in $profile_dir"
echo "Captured $regular_bv_count complete regular-bv workload profiles"
