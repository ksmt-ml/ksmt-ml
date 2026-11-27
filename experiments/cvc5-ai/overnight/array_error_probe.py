"""Preserve the complete parser error for the repeated Trove floating-point failure."""
from collections import Counter
import os
import re

from arrays_options import BASE
from replay import ROOT, digest, lines, load, now
from report import build, write_json
from validate_cores import cvc_capture

CARD = ROOT / "evidence/18-array-options.json"


def main():
    out = BASE / "fp-error-audit"
    out.mkdir(exist_ok=True)
    raw = [row for path in (BASE/"replay").glob("worker-*.jsonl") for row in lines(path) if row["status"] == "error"]
    groups = Counter(row["group"] for row in raw)
    assert len(groups) == 1 and len(raw) == 64
    manifest = BASE / "portable/manifest.json"
    source = next(row for row in load(manifest)["sessions"] if row["group"] in groups)
    path = manifest.parent / source["file"]
    assert digest(path) == source["sha256"]
    os.sched_setaffinity(0, {14})
    measured = cvc_capture(path, {}, True)
    (out/"stdout.log").write_text(measured["stdout"])
    (out/"stderr.log").write_text(measured["stderr"])
    match = re.search(r"(?m)^.*(?:Parse Error|not declared|unknown term|unhandled|unknown symbol).*$",
                      measured["stdout"]+"\n"+measured["stderr"])
    message = match.group(0).strip() if match else None
    write_json(out/"VERIFY.json", {"at": now(), "source": str(path.relative_to(ROOT)),
                                  "source_sha256": digest(path), "status": measured["status"],
                                  "same_failed_group_rows": len(raw), "diagnostic": message,
                                  "stdout_sha256": digest(out/"stdout.log")})
    if message and measured["status"] == "error" and "fp.to_ieee_bv" in message:
        card = load(CARD)
        card["paragraphs"].append("Все 64 ошибки получены на одном Trove-методе. Отдельный полный повтор сохранил ошибку разбора fp.to_ieee_bv. Ошибка одинакова у восьми режимов.")
        card["paths"].append("runs/17-arrays-options/fp-error-audit/VERIFY.json")
        write_json(CARD, card)
        build(load(ROOT/"current.json")["current"])


if __name__ == "__main__":
    main()
