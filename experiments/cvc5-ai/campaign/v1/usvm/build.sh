#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
src="$root/setup/usvm"
mkdir -p "$src" "$root/logs" "$root/setup/gradle" "$root/setup/java-tmp"
export GRADLE_USER_HOME="$root/setup/gradle" TMPDIR="$root/setup/java-tmp"
if [ ! -d "$src/.git" ]; then
  git -C "$src" init -q
  git -C "$src" fetch -q --depth=1 https://github.com/UnitTestBot/usvm 814a963354209421d36af758edbcec4295b081c4
  git -C "$src" checkout -q --detach FETCH_HEAD
  git -C "$src" apply "$root/setup/usvm-smt-logging.patch" "$root/setup/usvm-client-instrumentation.patch"
  cp "$root/v1/usvm/SessionTraceSolver.kt" "$src/usvm-core/src/main/kotlin/org/usvm/solver/"
  python3 - "$src/usvm-core/src/main/kotlin/org/usvm/solver/Solver.kt" <<'PY'
from pathlib import Path
import sys
f = Path(sys.argv[1])
s = f.read_text()
old = '.let { if (SmtQueryLog.enabled) LoggingSolver(ctx, it) else it }'
assert s.count(old) == 1
f.write_text(s.replace(old, '''.let {
            when {
                SessionTraceSolver.directory != null -> SessionTraceSolver(ctx, it)
                SmtQueryLog.enabled -> LoggingSolver(ctx, it)
                else -> it
            }
        }'''))
PY
fi
test "$(git -C "$src" rev-parse HEAD)" = 814a963354209421d36af758edbcec4295b081c4
cmp "$root/v1/usvm/SessionTraceSolver.kt" "$src/usvm-core/src/main/kotlin/org/usvm/solver/SessionTraceSolver.kt"
(
  cd "$src"
  timeout --kill-after=10 1550 ./gradlew --no-daemon --max-workers=3 \
    -Pkotlin.compiler.execution.strategy=in-process "-Dorg.gradle.jvmargs=-Xmx3g -Djava.io.tmpdir=$TMPDIR" \
    -I "$root/v1/usvm/classpath.init.gradle" :usvm-jvm:printTestCp -x detekt
) > "$root/logs/usvm-rebuild-$(date -u +%Y%m%dT%H%M%SZ).log"
log="$(ls -t "$root"/logs/usvm-rebuild-*.log | head -1)"
awk '/^(CP|APIJAR|APPROXJAR)=/' "$log" > "$root/setup/usvm-classpath.env"
test "$(wc -l < "$root/setup/usvm-classpath.env")" = 3
