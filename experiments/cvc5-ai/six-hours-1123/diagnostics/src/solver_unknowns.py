"""Classify solver UNKNOWN reasons and exceptional incomplete outcomes from verified rows."""
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "recovery/scripts"))
import base
from metrics import TIMEOUT, rows_from

STAGES = {
    "selection": ROOT / "recovery/raw/selection",
    "test-pre": ROOT / "recovery/raw/pre-restart",
    "test-post": ROOT / "recovery/raw/post-restart",
    "arithmetic-java": ROOT / "kernels/raw",
}


def label(reason):
    if "STORE_ALL" in reason:
        return "STORE_ALL"
    if TIMEOUT.search(reason):
        return "TIMEOUT"
    if "RESOURCEOUT" in reason.upper():
        return "RESOURCEOUT"
    return "other"


def main():
    tables = {}
    for split, path in STAGES.items():
        counts = {}
        for f in sorted(path.rglob("results.jsonl")):
            where = f.parent
            if not (where / "backend.json").is_file() or not (where / "status.json").is_file():
                continue
            inv = base.read(where / "invocation.json")
            arm = inv["arm"]
            if split == "selection":
                arm = str(f.relative_to(path)).split('/')[0]+"/"+("default" if not inv["config"] else "other")
            grouped = counts.setdefault(arm, dict(all=Counter(), exact=Counter(), projects={}, examples={}))
            for tag, r in rows_from(where).items():
                project = tag.partition("/")[0]
                if project == "kernel":
                    project = where.parent.name.rsplit("-r",1)[0]
                stats = r["stats"]
                grouped["all"].update(methods=1, checks=stats["checks"])
                project_counts = grouped["projects"].setdefault(project, Counter())
                project_counts.update(methods=1, checks=stats["checks"])
                assert stats["unknown"] == sum(stats["unknown_reasons"].values())
                for problem, n in stats["unknown_reasons"].items():
                    reason = label(problem)
                    grouped["all"][reason] += n
                    grouped["exact"][problem] += n
                    project_counts[reason] += n
                    grouped["examples"].setdefault(reason, dict(file=str(f.relative_to(ROOT)),tag=tag))
                if any("STORE_ALL" in k for k in stats["unknown_reasons"]):
                    grouped["all"]["methods_STORE_ALL"] += 1
                    project_counts["methods_STORE_ALL"] += 1
        tables[split]=counts
    out = ROOT / "diagnostics/evidence/solver-unknowns.json"
    base.write(out, tables)
    for split, table in tables.items():
        print(split,{a:dict(r["all"]) for a,r in table.items()})


if __name__ == "__main__":
    main()
