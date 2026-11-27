#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
name="$1"
shift
mkdir -p raw/phases
exec 9>"raw/phases/$name.lock"
flock --nonblock 9 || exit 73
stamp="$(date +%s%N)"
prefix="raw/phases/$name-$stamp"
date -Is > "$prefix.start"
printf '%q ' "$@" > "$prefix.command"
printf '\n' >> "$prefix.command"
printf '%s\n' "$$" > "$prefix.pid"
stop_log() {
    rc=$?
    printf '%s %s\n' "$(date -Is)" "$rc" > "$prefix.stop"
}
trap stop_log EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$@" > "$prefix.stdout" 2> "$prefix.stderr"
