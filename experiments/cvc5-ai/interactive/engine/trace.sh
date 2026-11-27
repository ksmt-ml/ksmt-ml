#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
target="$(realpath "$1")"
core="$2"
budget="$3"
method_limit="$4"
solver_limit="$5"
api="$(sed -n 's/^APIJAR=//p' "$root/../campaign/setup/usvm-classpath.env")"
approx="$(sed -n 's/^APPROXJAR=//p' "$root/../campaign/setup/usvm-classpath.env")"
cp="$(sed -n 's/^CP=//p' "$root/../campaign/setup/usvm-classpath.env")"
mkdir -p "$target/classes" "$target/traces" "$target/java-home" "$target/java-tmp"
exec 3>"$target/usvm-driver.log"
javac --release 17 -d "$target/classes" "$target"/source/pilot/*.java
export USVM_SESSION_DIR="$target/traces" USVM_SMT_DIR="$target/traces" USVM_SMT_MAX_PER_TAG=0
export MALLOC_ARENA_MAX=2 TMPDIR="$target/java-tmp"
cd "$target"
timeout --kill-after=5 "$budget" taskset -c "$core" env "usvm.jvm.api.jar.path=$api" "usvm.jvm.approximations.jar.path=$approx" \
  /usr/lib/jvm/java-21-openjdk/bin/java -Xmx2g -XX:ActiveProcessorCount=2 -XX:+UseSerialGC \
  "-Duser.home=$target/java-home" "-Djava.io.tmpdir=$TMPDIR" \
  -cp "$cp" org.usvm.dataset.DatasetDriverKt project=pilot cp="$target/classes" targets="$target/classes" \
  'pkg=pilot.' "methodTimeoutMs=$method_limit" "solverTimeoutMs=$solver_limit" seed=1871 \
  "totalSeconds=$((budget - 18))" maxMethodsPerClass=1 rssLimitMb=3500 >&3 2>&3
