"""Explain discordances in the original, paired live rows without additional filtering."""
import json
import re
from collections import Counter
from pathlib import Path
from common import ROOT, read, write

def group(rows):
    reasons = Counter()
    for row in rows:
        same_values = (row["branch"][0] == row["branch"][1] and
                       row["completed_paths"][0] == row["completed_paths"][1] and
                       row["cov"][0] == row["cov"][1])
        if same_values:
            reasons["same_recorded_coverage"] += 1
        elif "method_timeout" in row["stop"]:
            reasons["coverage_difference_with_time_guard"] += 1
        elif any(row["unknown"]):
            reasons["coverage_difference_with_solver_unknown"] += 1
        elif row["stop"][0] != row["stop"][1]:
            reasons["coverage_difference_with_other_stop"] += 1
        else:
            reasons["coverage_difference_same_stop_no_unknown"] += 1
    return reasons

def main():
    raw = ROOT / "runs/primary"
    m = read(raw / "metrics.json")
    schedule = read(ROOT / "inputs/schedule.json")["jobs"]
    failures = Counter()
    failure_samples = {}
    exercise = Counter()
    for job in schedule:
        for arm in job["arms"]:
            path = raw / job["id"] / arm / "results.jsonl"
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                row = json.loads(line)
                s = row["stats"]
                if row["status"] != "ok":
                    key = (arm, row["status"], (str(row["message"]).splitlines() or [""])[0][:180])
                    failures[key] += 1
                    failure_samples.setdefault(key,(row["tag"],str(path.parent.relative_to(ROOT))))
                else:
                    exercise[(arm, "solver_checks_present")] += s["checks"] > 0
                    exercise[(arm, "has_branches")] += bool(s["branch_edges"])
                    exercise[(arm, "has_terminal_paths")] += s["completed_paths"] > 0
    comps = {}
    for name, x in m["comparisons"].items():
        d = x["discordance"]["methods"]
        material = dict(instruction_pp=abs(x["coverage_pp"]["value"] or 0) > 2,
                        branches_rel=abs((x["branch_edges"]["value"] or 1)-1) > .05,
                        paths_rel=abs((x["completed_paths"]["value"] or 1)-1) > .05,
                        completion_pp=abs(x["completion_pp"]["value"] or 0) > 2,
                        repeated_local_change=any(abs(o["mean_coverage_diff"]) >= 10 or
                                                  o["edges_disagree"] >= 4 or
                                                  o["path_count_disagree"] >= 4 for o in d))
        comps[name] = dict(material=material,
                           context=group([row for meth in d for row in meth["rows"]]),
                           high_discordance=[{k:o[k] for k in ("tag","matches","mean_coverage_diff",
                               "edges_disagree","path_count_disagree","stop_disagree")}
                               for o in d if o["mean_coverage_diff"] != 0 or o["edges_disagree"] or
                                                o["path_count_disagree"] or o["stop_disagree"]][:8])
    write(raw / "diagnostics.json", dict(comparisons=comps,
        coverage_records_with_work={arm:{k:v for (a,k),v in exercise.items() if a==arm} for arm in m["arms"]},
        method_errors=[dict(arm=k[0], kind=k[1], message=k[2], n=n,
                            example=failure_samples[k]) for k,n in failures.most_common()]))
    print("Diagnosed comparisons:",", ".join(comps))

if __name__ == "__main__":
    main()
