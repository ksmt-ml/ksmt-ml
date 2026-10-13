#!/usr/bin/env bash
set -euo pipefail

readonly MAX_TRACKED_BYTES=$((5 * 1024 * 1024))
readonly FORBIDDEN_PREFIXES=(
  "data/raw/"
  "data/processed/"
  "artifacts/"
  "models/"
  "experiments/runs/"
  ".venv/"
)

failed=0

while IFS= read -r -d '' path; do
  for prefix in "${FORBIDDEN_PREFIXES[@]}"; do
    if [[ "$path" == "$prefix"* ]]; then
      printf 'forbidden generated or local path is tracked: %s\n' "$path" >&2
      failed=1
    fi
  done

  if [[ -f "$path" ]]; then
    size=$(wc -c <"$path")
    if (( size > MAX_TRACKED_BYTES )); then
      printf 'tracked file exceeds 5 MiB: %s (%s bytes)\n' "$path" "$size" >&2
      failed=1
    fi

    if [[ "$path" != "scripts/hygiene.sh" ]] &&
        grep -HnI -E '(/home/[^/]+/|/Users/[^/]+/|[A-Za-z]:\\Users\\)' "$path"; then
      printf 'absolute developer-local path found in tracked text: %s\n' "$path" >&2
      failed=1
    fi
  fi
done < <(git ls-files --cached --others --exclude-standard -z)

if (( failed != 0 )); then
  exit 1
fi

printf 'repository hygiene checks passed\n'
