"""Compare all three logic declarations on a single jointly correct cohort."""
from pathlib import Path
from statistics import geometric_mean
import sys

ROOT = Path(__file__).resolve().parents[1]
TOP = ROOT.parent
sys.path[:0] = [str(TOP / "followup/src"), str(TOP / "recovery/scripts")]
from analyze_power import cluster_ratio
import base


def main():
    plan = base.read(ROOT / "inputs/manifest.json")
    contrasts = (("B", "A"), ("U", "A"), ("U", "B"), ("C", "A"), ("C", "U"))
    paired = {f"{left}/{right}": [] for left, right in contrasts}
    for job in plan["jobs"]:
        path = ROOT / "evidence" / (job["id"] + ".json")
        if not path.is_file():
            continue
        family = plan["programs"][job["program"]]["family"]
        result = base.read(path)["results"]
        for values in zip(*(result[a]["cells"] for a in "ABCU"), strict=True):
            cells = dict(zip("ABCU", values, strict=True))
            if any(c["status"] != c["expected"] for c in values):
                continue
            for left, right in contrasts:
                paired[f"{left}/{right}"].append(dict(project=family,
                    cpu=[cells[a]["cpu_ns"] for a in (left, right)],
                    wall=[cells[a]["wall_ns"] for a in (left, right)]))
    summary = {}
    for label, rows in paired.items():
        timing = {key: cluster_ratio(rows, key) for key in ("cpu", "wall")}
        geometric = geometric_mean(timing["cpu"]["projects"].values()) if timing["cpu"].get("projects") else None
        summary[label] = dict(joint=len(rows), equal_family_cpu=geometric, **timing)
    base.write(ROOT / "evidence/pair-sensitivity.json", dict(analyzed=base.now(), contrasts=summary))


if __name__ == "__main__":
    main()
