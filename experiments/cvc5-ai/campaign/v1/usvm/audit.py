"""Audit recorded KSMT satisfiability against a separate pinned Z3 CLI process."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def audit(manifest, log, core):
    os.sched_setaffinity(0, {core})
    root = Path(__file__).resolve().parents[2]
    binary = root / ".venv/bin/z3"
    records = []
    for item in json.loads(manifest.read_text())["sessions"]:
        body = (manifest.parent / item["file"]).read_text()
        # Satisfiability is independent of inspecting the particular model/core.
        # Keep the assertion stack and all assumption checks exactly as replayed.
        audit_input = "\n".join(line for line in body.splitlines()
                                if not line.startswith(("(get-", "(set-option :produce-"))) + "\n"
        try:
            run = subprocess.run([str(binary), "-smt2", "-in", "-T:15"], input=audit_input,
                                 text=True, capture_output=True, timeout=16)
            status = "ok"
            out = run.stdout
            if run.returncode or "(error " in out:
                status = "error"
        except subprocess.TimeoutExpired as exc:
            status = "timeout"
            out = (exc.stdout or b"").decode()
        answers = re.findall(r"(?m)^(sat|unsat|unknown)\s*$", out)
        oracle = item["oracle"]
        if any(a != b and a != "unknown" for a, b in zip(answers, oracle)):
            status = "mismatch"
        elif status == "ok" and len(answers) != len(oracle):
            status = "timeout" if "timeout" in out else "incomplete"
        elif status == "ok" and "unknown" in answers:
            status = "unknown"
        row = {"group": item["group"], "session": item["file"], "status": status,
               "observed": len(answers), "expected": len(oracle), "answers": answers}
        if status != "ok":
            row["stdout_tail"] = out[-800:]
        with log.open("a") as f:
            f.write(json.dumps(row,sort_keys=True) + "\n")
        records.append(row)
    statuses = {s: sum(row["status"] == s for row in records) for s in sorted({r["status"] for r in records})}
    print(json.dumps(statuses))
    return not any(row["status"] in ("error", "mismatch") for row in records)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", type=Path)
    p.add_argument("log", type=Path)
    p.add_argument("--core", type=int, required=True)
    a = p.parse_args()
    raise SystemExit(0 if audit(a.manifest, a.log, a.core) else 1)
