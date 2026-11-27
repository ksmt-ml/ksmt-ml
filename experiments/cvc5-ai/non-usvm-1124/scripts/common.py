"""Local experiment bookkeeping and bounded, isolated subprocess runs."""
from datetime import datetime
import ctypes
import hashlib
import json
import os
from pathlib import Path
import random
import select
import signal
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MSK = ZoneInfo("Europe/Moscow")
DEFAULT_CUTOFF = datetime(2026, 11, 24, 11, 8, tzinfo=MSK)
if "CVC_CAMPAIGN_CUTOFF_MSK" in os.environ:
    replacement = datetime.fromisoformat(os.environ["CVC_CAMPAIGN_CUTOFF_MSK"])
    if replacement.tzinfo is None:
        raise ValueError("CVC_CAMPAIGN_CUTOFF_MSK needs an explicit UTC offset")
    CUTOFF = replacement.timestamp()
else:
    CUTOFF = DEFAULT_CUTOFF.timestamp()
CPUS = (12, 13, 14, 15)
CVC = ROOT / "deps/cvc5-Linux-x86_64-static/bin/cvc5"
YOSYS = ROOT / "deps/yosys/usr/bin/yosys"
BMC = ROOT / "deps/yosys/usr/bin/yosys-smtbmc"
Z3 = ROOT / "deps/dafny/z3/bin/z3-4.14.1"
DAFNY = ROOT / "deps/dafny/dafny"


def now():
    return datetime.now(MSK).isoformat(timespec="milliseconds")


def read(path):
    return json.loads(path.read_text())


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}")
    with tmp.open("w") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    os.replace(tmp, path)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


try:
    pidfd_open = os.pidfd_open
except AttributeError:
    # The pre-existing CPython 3.12 build used by SMAC lacks this binding.
    # Use the identical Linux kernel interface through the host libc.
    _pidfd_open = ctypes.CDLL(None, use_errno=True).pidfd_open
    _pidfd_open.argtypes = (ctypes.c_int, ctypes.c_uint)
    _pidfd_open.restype = ctypes.c_int

    def pidfd_open(pid):
        fd = _pidfd_open(pid, 0)
        if fd < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        return fd


def signal_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def run(where, cmd, cpu, cap, env=None, stdin=None):
    """Resume successful writes. Preserve interrupted attempts in adjacent folders."""
    if (where / "process.json").exists():
        row = read(where / "process.json")
        assert row["command"] == [str(x) for x in cmd] and row["cpu"] == cpu and row["cap"] == cap
        return row
    if where.exists():
        where.rename(where.with_name(where.name + f"-interrupted-{time.time_ns()}"))
    if time.time() + cap + 2 >= CUTOFF:
        raise TimeoutError("campaign collection deadline")
    if disk_used() > 7_500_000_000:
        raise RuntimeError("workspace disk budget")
    where.mkdir(parents=True)
    args = [str(x) for x in cmd]
    start = now()
    write(where / "start.json", dict(start=start, command=args, cpu=cpu, cap=cap))
    started = time.monotonic()
    timeout = False
    error = None
    ret = None
    with ((where / "stdout").open("wb") as out, (where / "stderr").open("wb") as err,
          tempfile.TemporaryFile(dir=where) as inp):
        if stdin is not None:
            inp.write(stdin)
            inp.seek(0)
        try:
            process = subprocess.Popen(["taskset", "-c", str(cpu), *args], cwd=where, env=env,
                                       stdin=inp if stdin is not None else subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True)
            pidfd = pidfd_open(process.pid)
            try:
                remaining = max(0, cap - (time.monotonic() - started))
                if not select.select([pidfd], [], [], remaining)[0]:
                    timeout = True
                    signal_group(process.pid, signal.SIGTERM)
                    if not select.select([pidfd], [], [], .25)[0]:
                        signal_group(process.pid, signal.SIGKILL)
                    # The leader can exit while solver descendants remain.
                    signal_group(process.pid, signal.SIGKILL)
                ret = process.wait()
            finally:
                os.close(pidfd)
        except OSError as exc:
            error = repr(exc)
    seconds = time.monotonic() - started
    row = dict(start=start, stop=now(), seconds=seconds, timeout=timeout, ret=ret,
               error=error, command=args, cpu=cpu, cap=cap, timer="linux-pidfd")
    write(where / "process.json", row)
    return row


def disk_used():
    check = ROOT / "evidence/disk-usage.json"
    try:
        row = read(check)
    except (OSError, ValueError):
        row = {"clock": 0}
    if time.time() - row["clock"] < 60:
        return row["bytes"]
    command = ["du", "-sx", "-B1", "--exclude=.*.[0-9]*.[0-9]*", str(ROOT)]
    # A live run atomically renames JSON files. Directory scans may see the
    # old entry disappear between readdir and lstat even after exclusions.
    for _ in range(3):
        usage = subprocess.run(command, capture_output=True, text=True)
        if usage.returncode == 0:
            break
        if "No such file or directory" not in usage.stderr:
            usage.check_returncode()
        if usage.stdout.split() and all("No such file or directory" in line
                                        for line in usage.stderr.splitlines()):
            break
        time.sleep(.02)
    if not usage.stdout.split():
        usage.check_returncode()
    size = int(usage.stdout.split()[0])
    if usage.returncode:
        with (ROOT / "evidence/disk-gauge-anomalies.jsonl").open("a") as file:
            file.write(json.dumps(dict(at=now(),status=usage.returncode,diagnostic=usage.stderr)) + "\n")
    write(check, {"clock": time.time(), "bytes": size, "at": now()})
    return size


def stable_seed(*parts):
    return int.from_bytes(hashlib.sha256(repr(parts).encode()).digest()[:8])


def ordered(items, *seed):
    result = list(items)
    random.Random(stable_seed(*seed)).shuffle(result)
    return result


def environment(**updates):
    return dict(os.environ, TZ="Europe/Moscow", LC_ALL="C.UTF-8",
                OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                PYTHONDONTWRITEBYTECODE="1", **updates)
