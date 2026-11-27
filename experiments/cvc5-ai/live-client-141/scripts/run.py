import argparse
import concurrent.futures
import json
import resource
import signal
import subprocess
import threading
import time
from datetime import datetime
from zipfile import ZipFile
from urllib.parse import urlparse, unquote

from common import *

CAP = 150
DISK_CAP = 12_000_000_000
HARD_STOP = datetime.fromisoformat("2026-11-22T19:42:43+03:00")
MAIN = "org.usvm.dataset.Live141DriverKt"
FIXTURE = BUILD / "livefixture.jar"
EVENT_LOCK = threading.Lock()
Z3_RESOURCES = BUILD / "z3-resources"
RUNTIME_CP = classpath([BUILD / "client", BUILD / "adapter", Z3_RESOURCES])

def disk_used():
    return int(subprocess.check_output(["du", "-s", "-B1", str(ROOT)], text=True, timeout=12).split()[0])

def append(path, event, **kwargs):
    with EVENT_LOCK:
        with path.open("a") as handle:
            handle.write(json.dumps(dict(event=event, at=now(), **kwargs), ensure_ascii=False) + "\n")

def prior_inputs(artifact):
    entries = [artifact["jar"], *artifact["dependencies"]]
    for e in entries:
        assert sha(e["path"]) == e["sha256"], e
    return os.pathsep.join(e["path"] for e in entries)

def once(path, arm, core, extra, tags=None, cap=CAP):
    path.mkdir(parents=True)
    scratch = path / "scratch"
    scratch.mkdir()
    method_file = path / "methods.txt"
    if tags is not None:
        method_file.write_text("\n".join(tags) + "\n")
        extra = [*extra, f"methods={method_file}"]
    env = safe_env(scratch)
    env.update(USVM_CLIENT_STATS="1", LIVE141_ARM=arm,
               USVM_SOLVER="z3" if arm == "z3" else "cvc5", USVM_TABLE_ITE="0",
               **{"usvm.jvm.api.jar.path": CP_ENV["APIJAR"], "usvm.jvm.approximations.jar.path": CP_ENV["APPROXJAR"]})
    cmd = ["taskset", "-c", str(core), *jvm(scratch), "-Dcvc5.skipLibraryLoad=true",
           f"-Dlive141.cvc5.jni={CVC5_JNI}", "-cp", RUNTIME_CP, MAIN,
           f"out={path}", f"fixture={FIXTURE}", *extra]
    write(path / "invocation.json", dict(cmd=cmd, core=core, jvm_cap_s=cap, tags=tags,
          env={k: env[k] for k in ("USVM_CLIENT_STATS", "LIVE141_ARM", "USVM_SOLVER",
               "USVM_TABLE_ITE", "LC_ALL", "TZ", "TMPDIR", "MALLOC_ARENA_MAX",
               "usvm.jvm.api.jar.path", "usvm.jvm.approximations.jar.path")}))
    start = now()
    t0 = time.monotonic_ns()
    timeout = False
    rc = None
    peak_rss = 0
    error = ""
    try:
        with (path / "stdout.log").open("wb") as stdout, (path / "stderr.log").open("wb") as stderr:
            child = subprocess.Popen(cmd, cwd=path, env=env, stdin=subprocess.DEVNULL,
                                     stdout=stdout, stderr=stderr, start_new_session=True)
            while True:
                remaining = cap - (time.monotonic_ns() - t0) / 1e9
                try:
                    rc = child.wait(timeout=max(0, min(remaining, 0.50)))
                    break
                except subprocess.TimeoutExpired:
                    if remaining <= 0:
                        timeout = True
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            rc = child.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            rc = child.wait(timeout=8)
                        break
                    try:
                        status = Path(f"/proc/{child.pid}/status").read_text()
                        peak_rss = max(peak_rss, int(next(s.split()[1] for s in status.splitlines()
                                                         if s.startswith("VmHWM:"))))
                    except (FileNotFoundError, ProcessLookupError, StopIteration):
                        pass
    except Exception as exc:
        error = repr(exc)
        if "child" in locals() and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            rc = child.wait()
    end = now()
    wall = (time.monotonic_ns() - t0) / 1e6
    rows = []
    result = path / "results.jsonl"
    if result.exists():
        with result.open() as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line)["tag"])
                except (json.JSONDecodeError, KeyError) as exc:
                    error += f" malformed output: {exc!r}"
    status = dict(arm=arm, core=core, start_msk=start, end_msk=end, wall_ms=wall,
                  returncode=rc, outer_timeout=timeout, cap_s=cap, supervisor_error=error,
                  peak_rss_kib_sampled=peak_rss, emitted=rows)
    write(path / "status.json", status)
    shutil.rmtree(scratch)
    return status

def release_evidence():
    archive = CAMPAIGN / "setup/cvc5.zip"
    assert sha(archive) == "2f8efe58fe27ba7bccbb504533f690b9312d69da14192712460e4a19231f02a1"
    with ZipFile(archive) as content:
        for p in (CVC5_JAR, CVC5_JNI, RELEASE / "bin/cvc5"):
            name = str(p.relative_to(RELEASE.parent))
            assert hashlib.sha256(content.read(name)).hexdigest() == sha(p), name
    z3 = {}
    with ZipFile(NATIVE_Z3_JAR) as content:
        for name in content.namelist():
            if not name.endswith(".so"):
                continue
            data = content.read(name)
            dest = Z3_RESOURCES / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(data)
            z3[dest.name] = hashlib.sha256(data).hexdigest()
            assert sha(dest) == z3[dest.name], dest
    assert set(z3) == {"libz3.so", "libz3java.so"}
    return dict(release_url="https://github.com/cvc5/cvc5/releases/tag/cvc5-1.4.1",
                archive_sha256=sha(archive), cvc5_jni_path=str(CVC5_JNI), cvc5_jni_sha256=sha(CVC5_JNI),
                cvc5_jar_sha256=sha(CVC5_JAR), cvc5_cli_sha256=sha(RELEASE / "bin/cvc5"),
                z3_native_jar_path=str(NATIVE_Z3_JAR), z3_native_jar_sha256=sha(NATIVE_Z3_JAR),
                z3_native_sha256=z3)

def verify_backend(path, arm, core, expected):
    b = read(path / "backend.json")
    assert b["arm"] == arm and b["cpu"] == str(core) and b["processors"] == 1, b
    if arm == "z3":
        assert {Path(p).name: h for p, h in b["libraries"].items()} == expected["z3_native_sha256"], b
        assert "ksmt-z3-core-0.5.26.jar" in b["adapter"] and b["version"].startswith("4."), b
        help_text = (path / "z3-help.txt").read_text()
        assert "random_seed" in help_text and "default: 0" in next(
            ln for ln in help_text.splitlines() if ln.startswith("random_seed ")), b
    else:
        assert b["version"] == "1.4.1" and Path(unquote(urlparse(b["java_api"]).path)) == CVC5_JAR, b
        assert Path(unquote(urlparse(b["adapter"]).path)) == BUILD / "adapter", b
        assert b["libraries"] == {str(CVC5_JNI): expected["cvc5_jni_sha256"]}, b
        value = "none" if arm == "cvc5-none" else b["simplification_before_override"]
        assert value and (arm != "cvc5-default" or value != "none"), b
        assert b["initial_options"]["simplification"] == b["after_first_native_check"]["simplification"] == value, b
        opts = b["after_first_native_check"]
        assert opts["tlimit-per"] == "1600" and opts["seed"] == "0", opts
        assert opts["incremental"] == opts["produce-models"] == opts["produce-unsat-cores"] == "true", opts
    return b

def cvc_option_delta(default, tuned, phase):
    one, two = default[phase], tuned[phase]
    return {k: [one.get(k), two.get(k)] for k in sorted(one.keys() | two.keys()) if one.get(k) != two.get(k)}

def smoke(attempt):
    path = ROOT / "runs/smoke" / attempt
    path.mkdir(parents=True)
    releases = release_evidence()
    write(path / "release.json", releases)
    cp = BUILD / "fixture-classes"
    cp.mkdir(exist_ok=True)
    cmd = [str(JDK / "javac"), "--release", "8", "-d", str(cp),
           str(ROOT / "checkout/smoke/src/livefixture/Control.java")]
    subprocess.run(["taskset", "-c", "14", *cmd], check=True, timeout=30)
    subprocess.run(["taskset", "-c", "14", str(JDK / "jar"), "--create", "--file", str(FIXTURE), "-C",
                    str(cp), "."], check=True, timeout=30)
    for kind, flag in (("contract", "contract=1"), ("live", "smoke=1")):
        for arm in ARMS:
            once(path / kind / arm, arm, 14, [flag], cap=75)
    certify_smoke(attempt)

def certify_smoke(attempt):
    path = ROOT / "runs/smoke" / attempt
    releases = read(path / "release.json")
    versions, checks = {}, {}
    for kind in ("contract", "live"):
        for arm in ARMS:
            run_dir = path / kind / arm
            result = read(run_dir / "status.json")
            try:
                assert result["returncode"] == 0, result
                stdout = (run_dir / "stdout.log").read_text()
                assert ("CONTRACT_PASS" if kind == "contract" else "LIVE_SMOKE_PASS") in stdout
                versions[kind, arm] = verify_backend(run_dir, arm, 14, releases)
                checks[f"{kind}/{arm}"] = dict(ok=True, version=versions[kind, arm]["version"])
            except Exception as e:
                checks[f"{kind}/{arm}"] = dict(ok=False, detail=str(e))
    differences = {}
    for kind in ("contract", "live"):
        if all((kind, a) in versions for a in ("cvc5-default", "cvc5-none")):
            diff = differences[kind] = {phase: cvc_option_delta(versions[kind, "cvc5-default"],
                versions[kind, "cvc5-none"], phase) for phase in ("initial_options", "after_first_native_check")}
            assert all(d == {"simplification": ["batch", "none"], "simplification-mode": ["batch", "none"]}
                       for d in diff.values()), diff
    help_text = subprocess.run([str(RELEASE / "bin/cvc5"), "--help"], text=True, timeout=10,
                               stdout=subprocess.PIPE, check=False).stdout
    alias_line = next(l.strip() for l in help_text.splitlines() if "--simplification=MODE | --simplification-mode=MODE" in l)

    ok = all(x["ok"] for x in checks.values()) and set(differences) == {"contract", "live"}
    write(path / "gate.json", dict(ok=ok, checked_at=now(), checks=checks, option_delta=differences, canonical_option_alias_evidence=alias_line))
    print("SMOKE", ok, path, flush=True)
    if not ok:
        raise SystemExit(1)

def benchmark(attempt, approved_smoke):
    smoke_root = ROOT / "runs/smoke" / approved_smoke
    assert read(smoke_root / "gate.json")["ok"]
    expected = read(smoke_root / "release.json")
    manifest = read(ROOT / "inputs/manifest.json")
    schedule = read(ROOT / "inputs/schedule.json")
    project = {p["project"]: p for p in manifest["projects"]}
    cps = {name: prior_inputs(p) for name, p in project.items()}
    out = ROOT / "runs" / attempt
    out.mkdir(parents=True)
    events = out / "supervisor.jsonl"
    write(out / "lock.json", dict(start=now(), approved_smoke=approved_smoke,
          prereg_sha256=sha(ROOT / "PREREG.md"), manifest_sha256=sha(ROOT / "inputs/manifest.json"),
          schedule_sha256=sha(ROOT / "inputs/schedule.json"), release=expected,
          runner_sha256=sha(Path(__file__)), fixture_sha256=sha(FIXTURE),
          adapter_classes={str(p.relative_to(BUILD)): sha(p) for p in (BUILD / "adapter").rglob("*.class")},
          client_classes={str(p.relative_to(BUILD)): sha(p) for p in (BUILD / "client").rglob("*.class")}))
    append(events, "campaign_start")
    blocked = threading.Event()

    def lane(core):
        for block in schedule["jobs"]:
            if block["core"] != core:
                continue
            if blocked.is_set() or datetime.now(TZ).timestamp() + CAP * 3 > HARD_STOP.timestamp():
                append(events, "block_unscheduled_deadline_or_gate", block=block["id"], core=core)
                continue
            size = disk_used()
            append(events, "block_start", block=block["id"], core=core, workspace_bytes=size)
            if size > DISK_CAP - 500_000_000 or shutil.disk_usage(ROOT).free < 2_000_000_000:
                blocked.set()
                append(events, "disk_guard", block=block["id"], workspace_bytes=size)
                continue
            for pos, arm in enumerate(block["arms"], start=1):
                where = out / block["id"] / arm
                append(events, "case_start", block=block["id"], arm=arm, position=pos, core=core)
                status = once(where, arm, core, [f"cp={cps[block['project']]}"], block["tags"])
                verified = False
                if (where / "backend.json").exists():
                    try:
                        verify_backend(where, arm, core, expected)
                        verified = True
                    except Exception as exc:
                        append(events, "identity_mismatch", block=block["id"], arm=arm, detail=str(exc))
                        blocked.set()
                append(events, "case_end", block=block["id"], arm=arm, position=pos, core=core,
                       identity_verified=verified, returncode=status["returncode"], emitted=len(status["emitted"]),
                       timeout=status["outer_timeout"], wall_ms=status["wall_ms"])
            append(events, "block_end", block=block["id"], core=core)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CORES)) as pool:
        tasks = [pool.submit(lane, core) for core in CORES]
        for task in concurrent.futures.as_completed(tasks):
            try:
                task.result()
            except BaseException as e:
                append(events, "lane_error", detail=repr(e))
                blocked.set()
    append(events, "campaign_end", workspace_bytes=disk_used(), guard=blocked.is_set())

if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("smoke", "certify-smoke", "benchmark"))
    parser.add_argument("attempt")
    parser.add_argument("--approved-smoke")
    args = parser.parse_args()
    if args.mode == "smoke":
        smoke(args.attempt)
    elif args.mode == "certify-smoke":
        certify_smoke(args.attempt)
    else:
        benchmark(args.attempt, args.approved_smoke)
