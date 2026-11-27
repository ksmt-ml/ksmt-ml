from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "recovery/scripts"))
import base

def main():
    base.setup_limits()
    build = ROOT/"build"
    out = ROOT/"evidence/build"
    if (out/"status.json").exists():
        assert base.read(out/"status.json")["returncode"]==0
        return
    out.mkdir(parents=True,exist_ok=True)
    for name in ("classes","scratch"):
        (build/name).mkdir(exist_ok=True)
    deps = ("kotlin-compiler-embeddable-2.1.0.jar","kotlin-stdlib-2.1.0.jar","kotlin-script-runtime-2.1.0.jar",
            "kotlin-reflect-1.6.10.jar","kotlin-daemon-embeddable-2.1.0.jar","trove4j-1.0.20200330.jar",
            "kotlinx-coroutines-core-jvm-1.6.4.jar","annotations-13.0.jar")
    compiler = os.pathsep.join(str(next(base.archive.MODULES.rglob(n))) for n in deps)
    friends = [base.OLD/"build/client",base.BUILD/"overlay",
               *[p for p in base.archive.LOCAL_CP if p.is_relative_to(base.archive.ORIGINAL)]]
    scratch = build/"scratch"
    sources = sorted((ROOT/"src").glob("*.kt"))
    cmd = ["taskset","-c","14",*base.archive.jvm(scratch,"1g"),"-cp",compiler,
           "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler","-no-stdlib","-no-reflect","-jvm-target","1.8",
           "-classpath",base.RUNTIME,"-Xfriend-paths="+",".join(map(str,friends)),"-d",str(build/"classes"),
           *map(str,sources)]
    base.write(out/"invocation.json",dict(cmd=cmd,sources={str(p):base.sha(p) for p in sources}))
    status = base.run_process(out,cmd,base.public_env(scratch),120)
    if status["returncode"] != 0:
        raise RuntimeError((status,(out/"stderr.log").read_text()))
    files = sorted(p for p in (build/"classes").rglob("*") if p.is_file())
    base.write(out/"runtime-sha256.json",{str(p):base.sha(p) for p in files})

if __name__=="__main__":
    main()
