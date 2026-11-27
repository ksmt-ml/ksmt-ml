"""Compile a local, option-only overlay of the original USVM solver factory."""
import os
from pathlib import Path

from corpus import JDK, commands_log
from live_client import config
from replay import CAMPAIGN, ROOT, digest, load, now
from report import write_json
from transfer import locate

BASE = ROOT / "runs/25-native-rewrite"
ORIGINAL = CAMPAIGN / "setup/usvm/usvm-jvm/src/main/kotlin/org/usvm/machine/JcSolverFactory.kt"
COMPILER = (
    "kotlin-compiler-embeddable-2.1.0.jar", "kotlin-stdlib-2.1.0.jar", "kotlin-script-runtime-2.1.0.jar",
    "kotlin-reflect-1.6.10.jar", "kotlin-daemon-embeddable-2.1.0.jar", "trove4j-1.0.20200330.jar",
    "kotlinx-coroutines-core-jvm-1.6.4.jar", "annotations-13.0.jar",
)
MARKER = "            val s = io.ksmt.solver.cvc5.KCvc5Solver(ctx)\n"
INSERT = """            val backend = s.nativeSolver()
            val requested = System.getenv("NIGHT_CVC5_OPTIONS").orEmpty()
            val applied = requested.split(',').filter { it.isNotBlank() }.map { opt ->
                val (key, value) = opt.split('=', limit = 2)
                backend.setOption(key, value)
                val actual = backend.getOption(key)
                check(actual == value) { "Native option mismatch for $key: $actual" }
                "$key=$actual"
            }
            if (!nightOptionsReported) {
                System.err.println("[night-native-options] " + backend.getVersion() + "|" + applied.joinToString(","))
                nightOptionsReported = true
            }
"""


def compile_overlay(core=14):
    compiler = BASE / "compile"
    gate = compiler / "VERIFY.json"
    if gate.exists():
        checked = load(gate)
        assert checked["original_sha256"] == digest(ORIGINAL)
        for name, expected in checked["classes"].items():
            assert digest(compiler / "classes" / name) == expected
        return compiler / "classes"
    compiler.mkdir(parents=True, exist_ok=True)
    tmp = compiler / "tmp"
    tmp.mkdir(exist_ok=True)
    home = compiler / "home"
    home.mkdir(exist_ok=True)
    source = compiler / ORIGINAL.name
    original = ORIGINAL.read_text()
    assert original.count(MARKER) == 1
    revised = original.replace("private object SameProcessSolverFactory : SolverFactory {",
                               "private object SameProcessSolverFactory : SolverFactory {\n"
                               "    private var nightOptionsReported = false")
    source.write_text(revised.replace(MARKER, MARKER + INSERT))
    jars = [locate(name) for name in COMPILER]
    cp = config()["CP"]
    friends = ",".join(entry for entry in cp.split(os.pathsep) if "/build/classes/kotlin/" in entry and Path(entry).is_dir())
    classes = compiler / "classes"
    command = ["taskset", "-c", str(core), str(JDK / "java"), "-XX:ActiveProcessorCount=1",
               "-XX:+UseSerialGC", "-XX:-UsePerfData", "-Xmx768m", f"-Djava.io.tmpdir={tmp}",
               f"-Duser.home={home}", "-cp", os.pathsep.join(map(str, jars)),
               "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler", "-no-stdlib", "-no-reflect",
               "-jvm-target", "1.8", "-classpath", cp, f"-Xfriend-paths={friends}", "-d",
               str(classes), str(source)]
    commands_log(compiler / "kotlinc.log", command, 45, cwd=compiler)
    files = {str(f.relative_to(classes)): digest(f) for f in sorted(classes.rglob("*.class"))}
    assert "org/usvm/machine/SameProcessSolverFactory.class" in files
    assert all(p.startswith("org/usvm/machine/") for p in files)
    write_json(gate, {"at": now(), "core": core, "original_sha256": digest(ORIGINAL),
                      "source_sha256": digest(source), "classes": files,
                      "compiler_jars": {f.name: digest(f) for f in jars},
                      "classpath_manifest_sha256": digest(CAMPAIGN / "setup/usvm-classpath.env")})
    return classes


if __name__ == "__main__":
    compile_overlay()
