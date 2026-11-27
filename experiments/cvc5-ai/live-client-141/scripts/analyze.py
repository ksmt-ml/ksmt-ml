import argparse
import csv
import random
import re
import statistics
from collections import Counter, defaultdict

from common import ARMS, ROOT, TZ, datetime, read, write

PAIRS = (("cvc5-none", "cvc5-default"), ("cvc5-default", "z3"), ("cvc5-none", "z3"))
PENALTY_UNIT_MS = 8000
TIMEOUT_RE = re.compile(r"time.?out|time.?limit|^canceled$", re.I)

def jsonl(path):
    if not path.exists():
        return []
    rows = []
    with path.open() as f:
        for line in f:
            try:
                rows.append(__import__("json").loads(line))
            except ValueError:
                if path.name != "results.jsonl":
                    raise
    return rows

def events(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        label, stamp, ms, tag = line.split("\t")
        rows.append(dict(label=label, at=stamp, ms=float(ms), tag=tag))
    return rows

def phases(evs, status):
    e = {x["label"]: x["ms"] for x in evs if x["label"] not in ("method_start", "method_end")}
    result = {}
    for label, start, end in (
        ("index_ms", "index_start", "index_end"),
        ("warmup_ms", "warmup_start", "warmup_end"),
        ("identity_ms", "warmup_end", "identity_end"),
        ("teardown_ms", "teardown_start", "driver_end"),
        ("prebatch_driver_ms", "driver_start", "batch_start"),
        ("batch_ms", "batch_start", "batch_end"),
    ):
        result[label] = e[end] - e[start] if start in e and end in e else None
    if status and e.get("driver_start") is not None:
        driver_at = datetime.fromisoformat(next(x["at"] for x in evs if x["label"] == "driver_start"))
        result["launch_to_driver_ms"] = (driver_at - datetime.fromisoformat(status["start_msk"])).total_seconds() * 1e3
    else:
        result["launch_to_driver_ms"] = None
    return result

def method_outcome(m, case, identity, started):
    if not case:
        return "not_run"
    if m is not None:
        if not identity:
            return "unverified_backend"
        if m["status"] != "ok":
            return "method_error"
        if m["stats"]["stop_reason"] == "method_timeout":
            return "method_timeout"
        return "budget_complete"
    if case["outer_timeout"]:
        return "outer_censored_started" if started else "outer_censored_not_started"
    if case["returncode"] != 0 or case["supervisor_error"]:
        return "jvm_failed_started" if started else "jvm_failed_not_started"
    return "missing"

def cap_score(obs, multiplier):
    return obs["row"]["wall_ms"] if obs["outcome"] == "budget_complete" else multiplier * PENALTY_UNIT_MS

def successful(obs):
    return obs["outcome"] in ("budget_complete", "method_timeout")

def mstat(obs, field, fallback=0):
    if not successful(obs):
        return fallback
    if field == "states":
        return obs["row"]["states"]
    if field == "branch_edges":
        return len(obs["row"]["stats"]["branch_edges"])
    return obs["row"]["stats"][field]

def percentile(values, q):
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * q
    i = int(index)
    return values[i] + (index - i) * (values[min(i + 1, len(values) - 1)] - values[i])

def ratio(a, b):
    return a/b if b else None

def estimate(rows, projects, bootstrap, divide=True):
    by_project = defaultdict(lambda: [0.0, 0.0, 0])
    for group, x, y in rows:
        agg = by_project[group]
        agg[0] += x
        agg[1] += y
        agg[2] += 1
    keys = [p for p in projects if by_project[p][2]]
    ids = [i for i, p in enumerate(projects) if p in keys]
    if not keys:
        return dict(value=None, ci=[None, None], n=0, clusters=0, loo=[None, None], by_project={})
    values = [by_project[p] for p in projects]
    a, b, n = (sum(row[j] for row in values) for j in range(3))
    def calc(a, b, n):
        return ratio(a, b) if divide else ((a-b)/n if n else None)
    by = {p: dict(value=calc(*by_project[p]), n=by_project[p][2]) for p in keys}
    sam = []
    for indices in bootstrap:
        x = y = count = 0
        for i in indices:
            v = values[i]
            x += v[0]
            y += v[1]
            count += v[2]
        val = calc(x, y, count)
        if val is not None:
            sam.append(val)
    loo = [calc(a-values[i][0], b-values[i][1], n-values[i][2]) for i in ids]
    loo = [v for v in loo if v is not None]
    return dict(value=calc(a, b, n), ci=[percentile(sam, .025), percentile(sam, .975)],
                n=n, clusters=len(keys), loo=[min(loo), max(loo)] if loo else [None, None], by_project=by)

def mean_values(xs):
    vals = [x for x in xs if x is not None]
    return statistics.mean(vals) if vals else None

def summary_arm(arm, obs, cases):
    pop = [m[arm] for m in obs.values()]
    case_rows = [x[arm] for x in cases.values()]
    ok = [o for o in pop if successful(o)]
    valid_stats = [o["row"]["stats"] for o in pop if o["row"] is not None and o["identity"]]
    attempts = [x for x in case_rows if x["status"]]
    good_attempts = [x for x in attempts if x["status"]["returncode"] == 0 and not x["status"]["supervisor_error"]
                     and x["identity"] and x["phases"]["batch_ms"] is not None]
    reasons = Counter()
    exceptions = Counter()
    for s in valid_stats:
        reasons.update(s["unknown_reasons"])
        exceptions.update(s["exception_types"])
    stats_totals = {key: sum(s[key] for s in valid_stats) for key in
                    ("checks", "checks_assume", "check_ms", "sat", "unsat", "unknown", "unknown_ms",
                     "model_calls", "model_ms", "create_solver_ms", "pushes", "pops", "unsat_core_calls",
                     "assert_ms", "translate_ms", "type_solver_ms", "decode_ms", "internal_check_ms")}
    observed_time = sum(o["row"]["wall_ms"] for o in pop if o["row"] is not None and o["identity"])
    return dict(
        intended=len(pop), observed=sum(o["row"] is not None for o in pop),
        started=sum(o["started"] for o in pop),
        statuses=dict(Counter(o["outcome"] for o in pop)),
        stop_reasons=dict(Counter(o["row"]["stats"]["stop_reason"] for o in ok)),
        emitted_error_statuses=dict(Counter(o["row"]["status"] for o in pop if o["row"] and o["outcome"] == "method_error")),
        completed_unique=len({o["tag"] for o in pop if o["outcome"] == "budget_complete"}),
        coverage_pp=mean_values([mstat(o, "coverage_pct", None) for o in pop]),
        coverage_missing_zero=mean_values([mstat(o, "coverage_pct") for o in pop]),
        coverage_metrics={key: sum(mstat(o, key) for o in pop) for key in
                          ("steps", "forking_steps", "states", "branch_edges", "terminated", "completed_paths",
                           "returned_paths")},
        unknown_reasons=dict(reasons),
        solver_unknown_timeout=sum(n for k, n in reasons.items() if TIMEOUT_RE.search(k)),
        solver_unknown_non_timeout=sum(n for k, n in reasons.items() if not TIMEOUT_RE.search(k)),
        terminal_exceptions=dict(exceptions), solver=stats_totals, measured_wall_total_ms=observed_time,
        check_share=ratio(stats_totals["check_ms"], observed_time),
        model_share=ratio(stats_totals["model_ms"], observed_time),
        method_wall_mean_ms=mean_values([o["row"]["wall_ms"] for o in ok]),
        penalties={str(k):mean_values([cap_score(o,k) for o in pop]) for k in (1,2,3)},
        jvm=dict(planned=len(case_rows), started=len(attempts), fully_completed=len(good_attempts),
                 verified=sum(x["identity"] for x in case_rows),
                 timeout=sum(x["status"]["outer_timeout"] for x in attempts),
                 failed=sum((x["status"]["returncode"] != 0 or bool(x["status"]["supervisor_error"]))
                            and not x["status"]["outer_timeout"] for x in attempts),
                 external_wall_mean_ms=mean_values([x["status"]["wall_ms"] for x in attempts]),
                 external_wall_sum_ms=sum(x["status"]["wall_ms"] for x in attempts),
                 peak_sample_mib=max((x["status"]["peak_rss_kib_sampled"] / 1024 for x in attempts), default=0),
                 means={k: mean_values([x["phases"][k] for x in attempts]) for k in
                        ("launch_to_driver_ms","index_ms","warmup_ms","identity_ms","prebatch_driver_ms","batch_ms","teardown_ms")},
                 batches_without_startup=sum(x["phases"]["batch_ms"] is not None for x in attempts)),
    )

def pair_summary(a, b, obs, cases, projects, bootstrap):
    intended = [(v[a]["project"],v[a],v[b]) for v in obs.values()]
    all_ok = [(p,x,y) for p,x,y in intended if successful(x) and successful(y)]
    completed = [(p,x,y) for p,x,y in all_ok if x["outcome"] == y["outcome"] == "budget_complete"]
    small = {
        "par": {str(n): estimate([(p,cap_score(x,n),cap_score(y,n)) for p,x,y in intended], projects, bootstrap)
                for n in (1,2,3)},
        "completed_wall": estimate([(p,x["row"]["wall_ms"],y["row"]["wall_ms"]) for p,x,y in completed],
                                   projects, bootstrap),
        "paired_emitted_wall": estimate([(p,x["row"]["wall_ms"],y["row"]["wall_ms"]) for p,x,y in all_ok],
                                        projects, bootstrap),
        "coverage_pp": estimate([(p,mstat(x,"coverage_pct"),mstat(y,"coverage_pct")) for p,x,y in all_ok],
                                projects, bootstrap, divide=False),
        "coverage_missing_zero_pp": estimate([(p,mstat(x,"coverage_pct"),mstat(y,"coverage_pct")) for p,x,y in intended],
                                             projects, bootstrap, divide=False),
        "completion_pp": estimate([(p,100*(x["outcome"]=="budget_complete"),100*(y["outcome"]=="budget_complete"))
                                   for p,x,y in intended], projects, bootstrap, divide=False),
        "jvm_external_wall": estimate([(x[a]["project"],x[a]["status"]["wall_ms"],x[b]["status"]["wall_ms"])
                                       for x in cases.values() if x[a]["status"] and x[b]["status"]],
                                      projects, bootstrap),
        "jvm_batch_wall": estimate([(x[a]["project"],x[a]["phases"]["batch_ms"],x[b]["phases"]["batch_ms"])
                                    for x in cases.values() if x[a]["phases"]["batch_ms"] is not None and
                                    x[b]["phases"]["batch_ms"] is not None], projects, bootstrap),
    }
    for key in ("states","branch_edges","terminated","completed_paths","returned_paths","steps","checks","check_ms"):
        small[key] = estimate([(p,mstat(x,key),mstat(y,key)) for p,x,y in all_ok],projects,bootstrap)
    # Per-method discrepancies are descriptive and retain the direction of all matched repeats.
    per_method = defaultdict(list)
    for p, x, y in all_ok:
        sx, sy = x["row"]["stats"], y["row"]["stats"]
        per_method[x["tag"]].append(dict(project=p, block=x["block"], outcome=[x["outcome"],y["outcome"]],
            cov=[sx["coverage_pct"],sy["coverage_pct"]], branch=[sx["branch_edges"],sy["branch_edges"]],
            check=[sx["checks"],sy["checks"]], completed_paths=[sx["completed_paths"],sy["completed_paths"]],
            exceptions=[sx["exception_types"],sy["exception_types"]], unknown=[sx["unknown_reasons"],sy["unknown_reasons"]],
            stop=[sx["stop_reason"],sy["stop_reason"]], steps=[sx["steps"],sy["steps"]],
            wall=[x["row"]["wall_ms"],y["row"]["wall_ms"]]))
    d = []
    for tag, rows in per_method.items():
        d.append(dict(tag=tag, matches=len(rows),
                      mean_coverage_diff=mean_values([r["cov"][0]-r["cov"][1] for r in rows]),
                      edges_disagree=sum(set(r["branch"][0]) != set(r["branch"][1]) for r in rows),
                      path_count_disagree=sum(r["completed_paths"][0] != r["completed_paths"][1] for r in rows),
                      stop_disagree=sum(r["stop"][0] != r["stop"][1] for r in rows), rows=rows))
    d.sort(key=lambda o: (-abs(o["mean_coverage_diff"]), -o["edges_disagree"], -o["path_count_disagree"], o["tag"]))
    small["discordance"] = dict(methods=d,
        branch_disagreement_occurrences=sum(o["edges_disagree"] for o in d),
        completed_path_count_disagreement_occurrences=sum(o["path_count_disagree"] for o in d),
        stop_disagreement_occurrences=sum(o["stop_disagree"] for o in d))
    return small

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("attempt", default="primary", nargs="?")
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    out = ROOT / "runs" / args.attempt
    schedule_path = out / "derived-schedule.json"
    schedule = read(schedule_path if schedule_path.exists() else ROOT / "inputs/schedule.json")["jobs"]
    projects = [x["project"] for x in read(ROOT / "inputs/manifest.json")["projects"]]
    journal = jsonl(out / "supervisor.jsonl")
    assert args.preview or journal and journal[-1]["event"] == "campaign_end"
    identity = {(r["block"],r["arm"]):r["identity_verified"] for r in journal if r["event"] == "case_end"}
    obs, cases = {}, {}
    for job in schedule:
        k = job["id"]
        cases[k] = {}
        for arm in job["arms"]:
            p = out / k / arm
            status = read(p / "status.json") if (p / "status.json").exists() else None
            event = events(p / "events.tsv")
            rows = {r["tag"]:r for r in jsonl(p / "results.jsonl")}
            # During a live preview avoid interpreting in-flight arms.
            verified = identity.get((k,arm), False)
            if not status:
                rows = {}
            starts = {r["tag"] for r in event if r["label"] == "method_start"}
            cases[k][arm] = dict(status=status, identity=verified, phases=phases(event,status), project=job["project"])
            for tag in job["tags"]:
                obs.setdefault((k,tag), {})[arm] = dict(block=k, tag=tag, project=job["project"], row=rows.get(tag),
                    identity=verified, started=tag in starts, outcome=method_outcome(rows.get(tag),status,verified,tag in starts))
    rng = random.Random(504141)
    bootstrap = [rng.choices(range(len(projects)),k=len(projects)) for _ in range(10000)]
    data = dict(analysis_for=args.attempt, projects=projects, methods=len({tag for _,tag in obs}),
        blocks=len(schedule), start=journal[0]["at"] if journal else None, end=journal[-1]["at"] if journal else None,
        elapsed_minutes=(datetime.fromisoformat(journal[-1]["at"])-datetime.fromisoformat(journal[0]["at"])).total_seconds()/60 if journal else None,
        max_sampled_disk_bytes=max((r.get("workspace_bytes",0) for r in journal),default=0),
        arms={a:summary_arm(a,obs,cases) for a in ARMS},
        comparisons={f"{a}/{b}":pair_summary(a,b,obs,cases,projects,bootstrap) for a,b in PAIRS},
        by_project={p:{a:summary_arm(a,{k:v for k,v in obs.items() if v[a]["project"]==p},
             {k:v for k,v in cases.items() if v[a]["project"]==p}) for a in ARMS} for p in projects})
    name = "preview" if args.preview else "metrics"
    write(out / f"{name}.json", data)
    if not args.preview:
        with (out / "method-outcomes.tsv").open("w") as f:
            writer=csv.writer(f,delimiter="\t")
            writer.writerow(["block","project","tag","arm","outcome","started","wall_ms","coverage_pct","states",
                             "branch_edges","completed_paths","checks","unknown"])
            for (k,tag), arms in obs.items():
                for a,o in arms.items():
                    row=o["row"]
                    writer.writerow([k,o["project"],tag,a,o["outcome"],o["started"],row["wall_ms"] if row else "",
                        *[mstat(o,key,None) for key in ("coverage_pct","states","branch_edges","completed_paths","checks","unknown")]])
    for a, r in data["arms"].items():
        print(a, r["statuses"], r["jvm"]["verified"], r["jvm"]["failed"], r["jvm"]["timeout"])
    for name, p in data["comparisons"].items():
        print(name, "PAR2", p["par"]["2"]["value"], p["par"]["2"]["ci"], "coverage", p["coverage_pp"]["value"])

if __name__ == "__main__":
    main()
