"""One real cvc5 CLI process per immutable incremental session."""
import hashlib
import json
from pathlib import Path
import re
import resource
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "setup/cvc5/cvc5-Linux-x86_64-static/bin/cvc5"
SPACE = {
    "bv-solver": ["auto", "bitblast", "bitblast-internal"],
    "bv-sat-solver": ["auto", "cadical"],
    "sat-solver": ["auto", "minisat", "cadical"],
    "bv-abstraction": ["auto", "on", "off"],
    "bv-eager-eval": ["auto", "on", "off"],
    "bv-eq-engine": ["auto", "on", "off"],
    "bv-gauss-elim": ["auto", "on", "off"],
    "bv-to-bool": ["auto", "on", "off"],
    "ite-simp": ["auto", "on", "off"],
    "repeat-simp": ["auto", "on", "off"],
    "simplification": ["auto", "none", "batch"],
    "minisat-simplification": ["auto", "clause-elim", "none"],
    "ext-rew-prep": ["auto", "off", "use", "agg"],
}
ANSWERS = re.compile(r"(?m)^(sat|unsat|unknown)\s*$")
LIMIT_AS = 2 * 1024**3
SOLVER_SEED = 18473
TUNING_TIMEOUT = 1.50
CHECKPOINTS = (0.25, 0.50, 0.75, 1.0)


def config(raw):
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a JSON object")
    for name, value in raw.items():
        if name not in SPACE or value not in SPACE[name]:
            raise ValueError(f"unsupported option or value: {name}={value!r}")
    return {name: value for name, value in sorted(raw.items()) if value != "auto"}


def flags(raw):
    result = []
    for name, value in config(raw).items():
        if value in ("on", "off") and SPACE[name] == ["auto", "on", "off"]:
            result.append(f"--{'' if value == 'on' else 'no-'}{name}")
        else:
            result.append(f"--{name}={value}")
    return result


def json_line(path, data):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()


def atomic_json(path, data):
    stage = path.with_name(path.name + ".new")
    stage.write_text(json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stage.replace(path)


def verify(manifest):
    data = json.loads(manifest.read_text())
    for item in data["sessions"]:
        path = manifest.parent / item["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"manifest mismatch for {path}")
    return data["sessions"]


def child_cpu():
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


def measure(manifest, item, params, timeout, deadline=None):
    path = manifest.parent / item["file"]
    limit = timeout
    if deadline is not None:
        limit = min(timeout, deadline - time.monotonic())
    if limit <= 0:
        return {"group": item["group"], "session": item["file"], "status": "budget", "answers": [], "queries": len(item["oracle"]),
                "correct_answers": 0, "wrong_answers": 0, "unknown_answers": 0,
                "wall_s": 0.0, "solver_cpu_s": 0.0, "par2": None}
    command = ["/usr/bin/prlimit", f"--as={LIMIT_AS}", "--core=0", "--",
               str(SOLVER), "--lang=smt2", "--incremental", "--quiet", f"--seed={SOLVER_SEED}",
               *flags(params), str(path)]
    cpu0 = child_cpu()
    start = time.monotonic()
    timed_out = False
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = p.communicate(timeout=max(0.001, limit - (time.monotonic() - start)))
    except subprocess.TimeoutExpired:
        timed_out = True
        p.kill()
        stdout, stderr = p.communicate()
    elapsed = time.monotonic() - start
    answers = ANSWERS.findall(stdout.decode(errors="replace"))
    oracle = item["oracle"]
    wrong = sum(a not in (b, "unknown") for a, b in zip(answers, oracle))
    correct = sum(a == b for a, b in zip(answers, oracle))
    unknown = answers.count("unknown")
    if wrong:
        status = "incorrect"
    elif timed_out:
        status = "timeout" if limit == timeout else "budget"
    elif p.returncode or len(answers) != len(oracle) or b'(error "' in stdout:
        status = "error"
    elif unknown:
        status = "unknown"
    elif elapsed > timeout:
        status = "timeout"
    else:
        status = "solved"
    row = {"group": item["group"], "session": item["file"], "family": item["family"], "status": status,
           "queries": len(oracle), "answers": answers, "correct_answers": correct,
           "wrong_answers": wrong, "unknown_answers": unknown, "rc": p.returncode,
           "wall_s": elapsed, "solver_cpu_s": child_cpu() - cpu0,
           "par2": None if status == "budget" else elapsed if status == "solved" else 2 * timeout}
    if stderr:
        row["stderr"] = stderr.decode(errors="replace")[:2000]
    if status == "error":
        row["stdout"] = stdout.decode(errors="replace")[:2000]
    return row


def aggregate(rows):
    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("solved", "timeout", "unknown", "error", "incorrect", "budget")}
    complete = bool(rows) and counts["budget"] == 0
    return {"counts": counts, "queries": sum(row["queries"] for row in rows),
            "correct_answers": sum(row["correct_answers"] for row in rows),
            "wrong_answers": sum(row["wrong_answers"] for row in rows),
            "unknown_answers": sum(row["unknown_answers"] for row in rows),
            "solver_cpu_s": sum(row["solver_cpu_s"] for row in rows),
            "measurement_wall_s": sum(row["wall_s"] for row in rows),
            "par2": sum(row["par2"] for row in rows) / len(rows) if complete else None,
            "eligible": complete and not (counts["incorrect"] or counts["error"]) }
