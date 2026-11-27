from urllib.parse import unquote, urlparse
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT.parent / "live-client-141"
sys.path.insert(0, str(OLD / "scripts"))
import common as archive
sys.path.pop(0)

TZ = ZoneInfo("Europe/Moscow")
CPUS = (12, 13, 14, 15)
CAP = 105
HEAP = "1500m"
RSS_KIB = 2_500_000
MAX_GROWTH = 12_000_000_000
BUILD = ROOT / "build"
RUNTIME = archive.classpath([BUILD / "overlay", OLD / "build/stock", OLD / "build/client",
                              OLD / "build/adapter", OLD / "build/z3-resources"])
SOURCE = OLD / "checkout/usvm/usvm-jvm/src/main/kotlin/org/usvm/machine/Live141Backend.kt"
EXPECTED = OLD / "runs/smoke/02/release.json"
DEFAULTS = {"arrays-exp":"false", "arrays-eager-index":"true", "bv-eager-eval":"false",
            "simplification":"batch", "static-learning":"true"}
SWITCH = {"arrays-exp":"true", "arrays-eager-index":"false", "bv-eager-eval":"true",
          "simplification":"none", "static-learning":"false"}
PENALTY = 16_000

now, read, sha, write = archive.now, archive.read, archive.sha, archive.write

def status(current, done, next_steps):
    write(ROOT / "current.json", dict(current=current, done=done, next=next_steps, updated=now()))

def public_env(scratch):
    env = archive.safe_env(scratch)
    for key in list(env):
        if key.startswith(("USVM_", "CVC5_", "LIVE141_", "LIVE_SEARCH_")):
            env.pop(key)
    env["XDG_DATA_HOME"] = str(scratch)
    env["XDG_CONFIG_HOME"] = str(scratch)
    return env

def size():
    return int(subprocess.check_output(["du","-s","-B1",str(ROOT)], timeout=15).split()[0])

def run_process(path, cmd, env, cap, stdin=None, rss_limit=RSS_KIB):
    """Keep subprocess streams and timestamped termination, including watchdog kills."""
    started = now()
    t0 = time.monotonic()
    reason = ""
    peak = 0
    rc = None
    try:
        with (path/"stdout.log").open("xb") as out, (path/"stderr.log").open("xb") as err:
            child = subprocess.Popen(cmd, cwd=path, env=env, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                     stdout=out, stderr=err, start_new_session=True)
            try:
                if stdin:
                    child.stdin.write(stdin)
                    child.stdin.close()
                while child.poll() is None:
                    elapsed = time.monotonic()-t0
                    if elapsed >= cap:
                        reason = "wall"
                        break
                    if rss_limit:
                        try:
                            hwm = next(l for l in Path(f"/proc/{child.pid}/status").read_text().splitlines()
                                       if l.startswith("VmHWM:"))
                            peak = max(peak, int(hwm.split()[1]))
                        except (FileNotFoundError, StopIteration, ProcessLookupError):
                            pass
                        if peak > rss_limit:
                            reason = "rss"
                            break
                    if any(f.stat().st_size > 8_000_000 for f in (path/"stdout.log", path/"stderr.log")):
                        reason = "log_cap"
                        break
                    time.sleep(min(0.15, max(0,cap-elapsed)))
            finally:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                rc = child.wait(timeout=8)
    except Exception as e:
        reason = reason or repr(e)
    record = dict(start_msk=started,end_msk=now(),wall_ms=(time.monotonic()-t0)*1000,
                  returncode=rc, watchdog=reason, cap_s=cap, peak_rss_kib=peak)
    write(path/"status.json",record)
    return record

def case(path, core, arm, config, seed, tags=(), classpath="", mode="measure", cap=CAP):
    path.mkdir(parents=True)
    scratch = path / "scratch"
    scratch.mkdir()
    env = public_env(scratch)
    env.update(USVM_CLIENT_STATS="1", LIVE141_ARM="z3" if arm=="z3" else "cvc5-default",
               USVM_SOLVER="z3" if arm=="z3" else "cvc5", USVM_TABLE_ITE="0",
               LIVE_SEARCH_OPTIONS=",".join(f"{k}={v}" for k,v in sorted(config.items())),
               **{"usvm.jvm.api.jar.path":archive.CP_ENV["APIJAR"],
                  "usvm.jvm.approximations.jar.path":archive.CP_ENV["APPROXJAR"]})
    cmd = ["taskset","-c",str(core),*archive.jvm(scratch, HEAP),
           "-Dcvc5.skipLibraryLoad=true",f"-Dlive141.cvc5.jni={archive.CVC5_JNI}",
           "-cp",RUNTIME,"org.usvm.dataset.LiveSearchDriverKt",f"out={path}",
           f"fixture={OLD/'build/livefixture.jar'}",f"seed={seed}"]
    if mode == "measure":
        (path/"methods.txt").write_text("\n".join(tags)+"\n")
        cmd += [f"cp={classpath}",f"methods={path/'methods.txt'}"]
    else:
        cmd.append(f"{mode}=1")
    fields = ("USVM_CLIENT_STATS","LIVE141_ARM","USVM_SOLVER","USVM_TABLE_ITE","LIVE_SEARCH_OPTIONS",
              "usvm.jvm.api.jar.path","usvm.jvm.approximations.jar.path","LC_ALL","TZ","TMPDIR")
    write(path/"invocation.json",dict(cmd=cmd,env={k:env[k] for k in fields},
          arm=arm,config=config,seed=seed,core=core,tags=list(tags),mode=mode))
    result = run_process(path,cmd,env,cap)
    shutil.rmtree(scratch)
    return result

def validate_backend(path, identity, invocation, expected):
    assert identity["arm"] == invocation["env"]["LIVE141_ARM"]
    assert identity["cpu"] == str(invocation["core"]) and identity["processors"] == 1
    assert identity["java_runtime"].startswith("21.")
    maps = (path/"backend-maps.txt").read_text().splitlines()
    mapped = {line[line.index("/"):].removesuffix(" (deleted)") for line in maps}
    assert mapped == set(identity["libraries"])
    if invocation["arm"] == "z3":
        assert identity["version"] == "4.13.0.0"
        assert {Path(k).name:v for k,v in identity["libraries"].items()} == expected["z3_native_sha256"]
        assert "ksmt-z3-core-0.5.26.jar" in identity["adapter"]
        help_rows = (path/"z3-help.txt").read_text().splitlines()
        assert any(s.startswith("random_seed ") and "default: 0" in s for s in help_rows)
    else:
        assert identity["version"] == "1.4.1"
        assert Path(unquote(urlparse(identity["java_api"]).path)) == archive.CVC5_JAR
        assert Path(unquote(urlparse(identity["adapter"]).path)) == OLD/"build/adapter"
        assert identity["libraries"] == {str(archive.CVC5_JNI):expected["cvc5_jni_sha256"]}
        options = DEFAULTS | invocation["config"]
        for phase in ("initial_options","after_first_native_check"):
            actual = identity[phase]
            assert all(actual[k] == v for k,v in options.items()), (path,phase,options)
            assert all(actual[k]=="true" for k in ("incremental","produce-models","produce-unsat-cores")), path
        assert identity["after_first_native_check"]["seed"] == "0"
        assert identity["after_first_native_check"]["tlimit-per"] == "1600"

def setup_limits():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(64_000_000,64_000_000))

def ensure_disk():
    if size() > 11_400_000_000 or shutil.disk_usage(ROOT).free < 1_000_000_000:
        raise RuntimeError("Сработал предел дискового пространства")
