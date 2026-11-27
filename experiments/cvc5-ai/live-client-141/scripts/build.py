import argparse
import subprocess
import sys
from pathlib import Path

from common import *

parser = argparse.ArgumentParser()
parser.add_argument("stage", choices=["adapter", "client"])
parser.add_argument("attempt")
args = parser.parse_args()
out = ROOT / "evidence" / f"build-{args.stage}-{args.attempt}.log"
assert not out.exists()
scratch = BUILD / "build-tmp"
scratch.mkdir(exist_ok=True)
adapter = BUILD / "adapter"
client = BUILD / "client"

compiler_names = (
    "kotlin-compiler-embeddable-2.1.0.jar", "kotlin-stdlib-2.1.0.jar",
    "kotlin-script-runtime-2.1.0.jar", "kotlin-reflect-1.6.10.jar",
    "kotlin-daemon-embeddable-2.1.0.jar", "trove4j-1.0.20200330.jar",
    "kotlinx-coroutines-core-jvm-1.6.4.jar", "annotations-13.0.jar",
)
compiler = [next(MODULES.rglob(name)) for name in compiler_names]
env = safe_env(scratch)

with out.open("x") as log:
    print("START", now(), file=log, flush=True)
    def run(cmd, max_time=110):
        print("CMD", json.dumps(list(map(str, cmd))), file=log, flush=True)
        subprocess.run(["taskset", "-c", "14", *map(str, cmd)], cwd=ROOT, env=env,
                       stdout=log, stderr=subprocess.STDOUT, timeout=max_time, check=True)

    def kotlinc(cp, dest, sources, friends=()):
        args = [*jvm(scratch, "1g"), "-cp", os.pathsep.join(map(str, compiler)),
                "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler", "-no-stdlib", "-no-reflect",
                "-jvm-target", "1.8", "-classpath", cp, "-d", dest]
        if friends:
            args.append("-Xfriend-paths=" + ",".join(map(str, friends)))
        run([*args, *sources])

    try:
        if args.stage == "adapter":
            adapter.mkdir(exist_ok=True)
            run([JDK / "javac", "--release", "8", "-cp", CVC5_JAR, "-d", adapter,
                 KSMT / "live-shim/io/github/cvc5/PointerAccess.java"])
            source = KSMT / "ksmt-cvc5/ksmt-cvc5-core/src/main/kotlin"
            kotlinc(classpath([adapter]), adapter, sorted(source.rglob("*.kt")))
        else:
            client.mkdir(exist_ok=True)
            main = USVM / "usvm-jvm/src/main/kotlin/org/usvm/machine"
            core = USVM / "usvm-core/src/main/kotlin/org/usvm/solver"
            test = USVM / "usvm-jvm/src/test/kotlin/org/usvm/dataset"
            friends = [p for p in LOCAL_CP if str(p).startswith(str(ORIGINAL)) and p.exists()]
            kotlinc(classpath([adapter]), client,
                    [main / "JcSolverFactory.kt", main / "JcMachine.kt", main / "Live141Backend.kt", core / "ClientStats.kt",
                     core.parent / "stopstrategies/StopStrategyFactory.kt",
                     test / "Live141Driver.kt", test / "Live141Contract.kt"], friends)
        print("OK", now(), file=log, flush=True)
    except BaseException as exc:
        print("FAILED", now(), repr(exc), file=log, flush=True)
        raise
