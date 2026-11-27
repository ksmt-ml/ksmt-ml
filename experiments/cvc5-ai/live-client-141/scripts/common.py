import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT.parent / "campaign"
OVERNIGHT = ROOT.parent / "overnight"
ORIGINAL = CAMPAIGN / "setup/usvm"
USVM = ROOT / "checkout/usvm"
KSMT = ROOT / "checkout/ksmt"
JDK = Path("/usr/lib/jvm/java-21-openjdk/bin")
RELEASE = CAMPAIGN / "setup/cvc5/cvc5-Linux-x86_64-static"
CVC5_JAR = RELEASE / "share/java/cvc5-1.4.1.jar"
CVC5_JNI = RELEASE / "lib/libcvc5jni.so"
CP_ENV = dict(line.split("=", 1) for line in
              (CAMPAIGN / "setup/usvm-classpath.env").read_text().splitlines() if "=" in line)
INSTALLED_CP = [Path(p) for p in CP_ENV["CP"].split(os.pathsep)]
LOCAL_CP = [p for p in INSTALLED_CP if not p.name.startswith("ksmt-cvc5")]
NATIVE_Z3_JAR = next(p for p in LOCAL_CP if p.name == "ksmt-z3-native-linux-x64-0.5.26.jar")
MODULES = CAMPAIGN / "setup/gradle/caches/modules-2/files-2.1"
BUILD = ROOT / "build"
CORES = (12, 13, 14, 15)
ARMS = ("z3", "cvc5-default", "cvc5-none")
TZ = ZoneInfo("Europe/Moscow")

def now():
    return datetime.now(TZ).isoformat(timespec="milliseconds")

def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def read(path):
    return json.loads(Path(path).read_text())

def write(path, obj):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)

def safe_env(scratch):
    env = dict(os.environ)
    for key in ("USVM_SESSION_DIR", "USVM_SMT_DIR", "USVM_TABLE_ITE", "NIGHT_CVC5_OPTIONS",
                "JDK_JAVA_OPTIONS", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS",
                "LD_PRELOAD", "LD_LIBRARY_PATH", "CLASSPATH", "BASH_ENV"):
        env.pop(key, None)
    env.update(TMPDIR=str(scratch), XDG_CACHE_HOME=str(scratch), LC_ALL="C.UTF-8", TZ="Europe/Moscow",
               MALLOC_ARENA_MAX="2")
    return env

def classpath(extra=()):
    return os.pathsep.join(str(p) for p in [*extra, CVC5_JAR, *LOCAL_CP])

def jvm(scratch, heap="2g"):
    return [str(JDK / "java"), f"-Xmx{heap}", "-XX:ActiveProcessorCount=1",
            "-XX:+UseSerialGC", "-XX:-UsePerfData", "-XX:-CreateCoredumpOnCrash", "-Dfile.encoding=UTF-8",
            "-Duser.language=en", "-Duser.country=US", "-Duser.timezone=Europe/Moscow",
            f"-Djava.io.tmpdir={scratch}", f"-Duser.home={scratch}",
            f"-XX:ErrorFile={scratch.parent}/hs_err_pid%p.log"]
