from base import *

def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(old)
    return source.replace(old,new)

def main():
    setup_limits()
    for dest in ("overlay","src","tmp"):
        (BUILD/dest).mkdir(parents=True,exist_ok=True)
    output = ROOT/"raw/build-02"
    output.mkdir()
    source = SOURCE.read_text()
    source = replace_once(source, '    private var initial = emptyMap<String, String>()',
        '''    private val overrides = System.getenv("LIVE_SEARCH_OPTIONS").orEmpty()
        .split(',').filter { it.isNotBlank() }
        .associate { it.substringBefore('=') to it.substringAfter('=') }
    private var initial = emptyMap<String, String>()''')
    source = replace_once(source, '''            if (arm == "cvc5-none") native.setOption("simplification", "none")
            check(native.getOption("simplification") == if (arm == "cvc5-none") "none" else before)''',
        '''            for ((key, value) in overrides) {
                native.setOption(key, value)
                check(native.getOption(key) == value) { "Unexpected option $key" }
            }''')
    backend = BUILD/"src/Live141Backend.kt"
    backend.write_text(source)
    driver = BUILD/"src/LiveSearchDriver.kt"
    driver.write_text(replace_once((OLD/"build/stock/Live141Stock.kt").read_text(),
                                  "randomSeed = 0,", 'randomSeed = cfg.getValue("seed").toLong(),'))
    names = ("kotlin-compiler-embeddable-2.1.0.jar","kotlin-stdlib-2.1.0.jar","kotlin-script-runtime-2.1.0.jar",
             "kotlin-reflect-1.6.10.jar","kotlin-daemon-embeddable-2.1.0.jar","trove4j-1.0.20200330.jar",
             "kotlinx-coroutines-core-jvm-1.6.4.jar","annotations-13.0.jar")
    compiler = os.pathsep.join(str(next(archive.MODULES.rglob(n))) for n in names)
    friends = [OLD/"build/client",*[p for p in archive.LOCAL_CP if str(p).startswith(str(archive.ORIGINAL))]]
    scratch = BUILD/"tmp"
    cmd = ["taskset","-c","14",*archive.jvm(scratch,"1g"),"-cp",compiler,
           "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler","-no-stdlib","-no-reflect","-jvm-target","1.8",
           "-classpath",RUNTIME,"-Xfriend-paths="+",".join(map(str,friends)),"-d",str(BUILD/"overlay"),
           str(backend),str(driver)]
    write(output/"invocation.json",cmd)
    assert run_process(output,cmd,public_env(scratch),110)["returncode"]==0
    files = [*sorted((BUILD/"src").glob("*.kt"))]
    for d in [BUILD/"overlay",OLD/"build/stock",OLD/"build/client",OLD/"build/adapter",OLD/"build/z3-resources"]:
        files.extend(p for p in sorted(d.rglob("*")) if p.is_file())
    files += [OLD/"build/livefixture.jar",archive.CVC5_JAR,archive.CVC5_JNI,archive.JDK/"java"]
    write(ROOT/"evidence/runtime.json",{str(p):sha(p) for p in files})

if __name__=="__main__":
    main()
