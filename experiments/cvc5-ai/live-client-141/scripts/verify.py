"""Reconstruct the intended observations and verify the primary estimates from raw files."""
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from itertools import permutations
from pathlib import Path
from zipfile import ZipFile

WORK = Path(__file__).resolve().parents[1]
INPUT = WORK / "inputs"
RAW = WORK / "runs" / (sys.argv[1] if len(sys.argv) > 1 else "primary")
MODES = ("z3", "cvc5-default", "cvc5-none")
SMOKE_NAMES = ("contract", "live")
VALID_REASON = {"queue_exhausted", "step_limit", "coverage_goal", "method_timeout",
                "stagnation", "targets", "states_limit"}

def load(path):
    return json.loads(path.read_text())

def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()

def lines(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines()]

def dt(value):
    return datetime.fromisoformat(value)

def same(a,b, tol=1e-10):
    return math.isclose(a,b,rel_tol=tol,abs_tol=tol)

def check_stats(row):
    s = row["stats"]
    assert 0 <= s["coverage_pct"] <= 100
    assert row["wall_ms"] >= row["analyze_ms"] + row["construct_ms"] - .001
    assert row["states"] >= 0 and row["cpu_ms"] >= 0
    assert s["stop_reason"] in VALID_REASON
    assert s["sat"] + s["unsat"] + s["unknown"] == s["checks"]
    assert s["checks"] == len(s["check_ms_list"]) == len(s["assert_ms_per_check_list"])
    assert s["checks_assume"] <= s["checks"]
    assert sum(s["unknown_reasons"].values()) == s["unknown"]
    assert s["completed_paths"] <= s["terminated"]
    assert s["returned_paths"] + sum(s["exception_types"].values()) <= s["completed_paths"]
    assert s["forking_steps"] <= s["steps"]
    assert s["steps"] <= 4001
    assert len(s["branch_edges"]) == len(set(s["branch_edges"]))
    assert all(re.fullmatch(r"-?\d+:-?\d+", x) for x in s["branch_edges"])
    assert abs(s["check_ms"] - sum(s["check_ms_list"])) < .002 + .0005*s["checks"]
    if s["stop_reason"] == "step_limit":
        assert s["steps"] == 4001

def get_status_vars(row, case, verified, started):
    if case is None:
        return "not_run"
    if row:
        if not verified: return "unverified_backend"
        if row["status"] != "ok": return "method_error"
        return "method_timeout" if row["stats"]["stop_reason"] == "method_timeout" else "budget_complete"
    suffix = "_started" if started else "_not_started"
    if case["outer_timeout"]: return "outer_censored" + suffix
    if case["returncode"] != 0 or case["supervisor_error"]: return "jvm_failed" + suffix
    return "missing"

def audit_asset(ref):
    command = load(WORK / "inputs/manifest.json")
    for p in command["projects"]:
        for j in (p["jar"], *p["dependencies"]):
            assert sha(Path(j["path"])) == j["sha256"]
    for f,h in command["prior_files"].items():
        assert sha(Path(f)) == h
    lib = Path(ref["cvc5_jni_path"])
    zip_path = lib.parents[3] / "cvc5.zip"
    assert sha(zip_path) == ref["archive_sha256"]
    assert sha(lib) == ref["cvc5_jni_sha256"]
    with ZipFile(zip_path) as archive:
        base = Path(lib.parent.parent.name)
        for name, key in ((base/"lib/libcvc5jni.so","cvc5_jni_sha256"),
                           (base/"share/java/cvc5-1.4.1.jar","cvc5_jar_sha256"),
                           (base/"bin/cvc5","cvc5_cli_sha256")):
            assert hashlib.sha256(archive.read(str(name))).hexdigest() == ref[key]
    jar = Path(ref["z3_native_jar_path"])
    assert sha(jar) == ref["z3_native_jar_sha256"]
    with ZipFile(jar) as archive:
        for entry in archive.namelist():
            if entry.endswith(".so"):
                assert hashlib.sha256(archive.read(entry)).hexdigest() == ref["z3_native_sha256"][Path(entry).name]
                assert sha(WORK / "build/z3-resources" / entry) == ref["z3_native_sha256"][Path(entry).name]

def backend(path, arm, core, assets):
    b = load(path/"backend.json")
    assert b["arm"] == arm and b["cpu"] == str(core) and b["processors"] == 1
    maps = (path/"backend-maps.txt").read_text().splitlines()
    mapped = {x[x.index("/"):].removesuffix(" (deleted)") for x in maps}
    assert mapped == set(b["libraries"])
    assert all(sha(Path(p)) == h for p,h in b["libraries"].items())
    if arm == "z3":
        assert b["version"] == "4.13.0.0"
        assert {Path(k).name:v for k,v in b["libraries"].items()} == assets["z3_native_sha256"]
        assert re.search(r"^random_seed .+\(default: 0\)", (path/"z3-help.txt").read_text(), re.M)
    else:
        assert b["version"] == "1.4.1"
        assert b["libraries"] == {assets["cvc5_jni_path"]:assets["cvc5_jni_sha256"]}
        assert b["simplification_before_override"] == "batch"
        assert ("/build/adapter/" in b["adapter"] and b["java_api"].endswith("/cvc5-1.4.1.jar"))
        expect = "batch" if arm == "cvc5-default" else "none"
        assert b["initial_options"]["simplification"] == expect
        values = b["after_first_native_check"]
        assert all(values[k] == "true" for k in ("incremental","produce-models","produce-unsat-cores","fp-exp"))
        assert values["simplification"] == expect and values["seed"] == "0" and values["tlimit-per"] == "1600"
    return b

def verify_option_delta(a, b):
    keys = {"simplification", "simplification-mode"}
    for field in ("initial_options", "after_first_native_check"):
        x,y = a[field], b[field]
        assert x.keys() == y.keys()
        assert {k for k in x if x[k] != y[k]} == keys
        assert all((x[k],y[k]) == ("batch","none") for k in keys)

def quantile(x, fraction):
    n = (len(x)-1)*fraction
    lo,hi = math.floor(n),math.ceil(n)
    return x[lo]*(hi-n) + x[hi]*(n-lo) if lo != hi else x[lo]

def main():
    lock = load(RAW/"lock.json")
    assets = lock["release"]
    manifest = load(INPUT/"manifest.json")
    schedule = load(INPUT/"schedule.json")
    metrics = load(RAW/"metrics.json")
    journal = lines(RAW/"supervisor.jsonl")
    assert journal[0]["event"] == "campaign_start" and journal[-1]["event"] == "campaign_end"
    assert all(dt(a["at"]) <= dt(b["at"]) for a,b in zip(journal,journal[1:]))
    assert dt(lock["start"]) <= dt(journal[0]["at"]) < dt(journal[-1]["at"])
    assert all(dt(x["at"]).utcoffset().total_seconds() == 10800 for x in journal)
    assert metrics["start"] == journal[0]["at"] and metrics["end"] == journal[-1]["at"]
    assert dt(journal[-1]["at"]) < dt("2026-11-22T19:52:43+03:00")
    assert max(r.get("workspace_bytes",0) for r in journal) < 12_000_000_000
    for p in ("manifest","schedule"):
        assert lock[f"{p}_sha256"] == sha(INPUT/f"{p}.json")
    for what, field in (("PREREG.md","prereg_sha256"),("build/livefixture.jar","fixture_sha256"),
                        ("scripts/run.py","runner_sha256")):
        assert lock[field] == sha(WORK/what)
    assert dt((WORK/"evidence/prereg-msk.txt").read_text().strip()) < dt(manifest["locked"]) < dt(lock["start"])
    for kind in ("adapter_classes","client_classes"):
        for path,digest in lock[kind].items():
            assert sha(WORK/"build"/path) == digest
    audit_asset(assets)
    smoke = WORK/"runs/smoke"/lock["approved_smoke"]
    assert load(smoke/"gate.json")["ok"]
    for kind in SMOKE_NAMES:
        rec = {}
        for arm in MODES:
            p = smoke/kind/arm
            assert load(p/"status.json")["returncode"] == 0
            out = (p/"stdout.log").read_text()
            if kind=="contract":
                assert "CONTRACT_PASS scalar fp array detach after close scopes assumptions unsat core" in out
            else:
                assert "LIVE_SMOKE_PASS" in out
                assert sorted(int(n) for n in re.findall(r"^WITNESS scalar .* returns=(-?\d+)$",out,re.M)) == [-11,22,33]
                for m in lines(p/"warmup.jsonl"): check_stats(m)
            rec[arm] = backend(p,arm,14,assets)
        verify_option_delta(rec["cvc5-default"],rec["cvc5-none"])

    projects = [p["project"] for p in manifest["projects"]]
    tags = {p["project"]:{m["tag"] for m in p["methods"]} for p in manifest["projects"]}
    for p in manifest["projects"]:
        assert len(tags[p["project"]]) == 8
        assert len(set(m["class"] for m in p["methods"])) >= (4 if p["project"] == "re2j" else 8)
        assert sum(m["bytecode_branches"] in (1,2) for m in p["methods"]) == 4
        prior = (INPUT/(p["project"]+"-eligible.tsv")).read_text()
        for m in p["methods"]:
            cls, name_desc = m["tag"].split("/",1)[1].rsplit(".",1)
            name, desc = name_desc.split("(",1)
            assert f"{cls}\t{name}\t({desc}\t{m['bytecode_instructions']}\t{m['bytecode_branches']}\n" in prior
    for project in projects:
        jobs = [j for j in schedule["jobs"] if j["project"] == project]
        assert len(jobs) == 6
        assert set(tuple(j["arms"]) for j in jobs) == set(permutations(MODES))
        assert all(set(j["tags"]) == tags[project] for j in jobs)
        assert max(Counter(j["core"] for j in jobs).values()) <= 2
    assert Counter(j["core"] for j in schedule["jobs"]) == {12:15,13:15,14:15,15:15}

    counts = {a:Counter() for a in MODES}
    stop_counts = {a:Counter() for a in MODES}
    totals = {a:Counter() for a in MODES}
    coverage = {a:{} for a in MODES}
    wall = {a:defaultdict(list) for a in MODES}
    raw_hashes = {}
    intervals = defaultdict(list)
    identity = {}
    opts = set()
    jvm_outcomes = defaultdict(Counter)
    warmup_outcomes = defaultdict(Counter)
    case_events = {(x["block"],x["arm"]):x for x in journal if x["event"]=="case_end"}
    starts = {(x["block"],x["arm"]):x for x in journal if x["event"]=="case_start"}
    assert len(case_events) == sum(x["event"]=="case_end" for x in journal)
    assert len(starts) == sum(x["event"]=="case_start" for x in journal)
    for job in schedule["jobs"]:
        verified_here = {}
        prior_end = None
        for i,arm in enumerate(job["arms"],1):
            p = RAW/job["id"]/arm
            status = load(p/"status.json") if (p/"status.json").exists() else None
            key = job["id"],arm
            case_end = case_events.get(key)
            verified = bool(case_end and case_end["identity_verified"])
            if status:
                inv = load(p/"invocation.json")
                case_start = starts[key]
                assert inv["core"] == case_start["core"] == status["core"] == case_end["core"] == job["core"]
                assert case_start["position"] == case_end["position"] == i
                assert status["arm"] == arm and status["cap_s"] == 150
                assert inv["env"]["USVM_SOLVER"] == ("z3" if arm=="z3" else "cvc5")
                assert inv["env"]["USVM_CLIENT_STATS"] == "1" and inv["env"]["USVM_TABLE_ITE"] == "0"
                assert "jvm" in inv["env"]["usvm.jvm.api.jar.path"] and inv["tags"] == job["tags"]
                assert (p/"methods.txt").read_text().splitlines() == job["tags"]
                start,end = dt(status["start_msk"]),dt(status["end_msk"])
                assert dt(case_start["at"]) <= start <= end <= dt(case_end["at"])
                assert abs(1e3*(end-start).total_seconds()-status["wall_ms"]) < 5
                assert not prior_end or prior_end <= start
                prior_end = end
                intervals[job["core"]].append((start,end))
                assert status["outer_timeout"] == case_end["timeout"]
                assert status["returncode"] == case_end["returncode"]
                jvm_outcomes[arm]["started"] += 1
                jvm_outcomes[arm]["verified"] += verified
                jvm_outcomes[arm]["timeout"] += status["outer_timeout"]
                jvm_outcomes[arm]["failed"] += bool(status["returncode"] or status["supervisor_error"]) and not status["outer_timeout"]
            if verified:
                verified_here[arm] = backend(p,arm,job["core"],assets)
                identity[arm] = verified_here[arm]["version"]
                opts.add((p/"usvm-options.txt").read_text())
            evs = [line.split("\t") for line in (p/"events.tsv").read_text().splitlines()] if (p/"events.tsv").exists() else []
            assert all(float(x[2]) <= float(y[2]) for x,y in zip(evs,evs[1:]))
            if verified:
                warm = lines(p/"warmup.jsonl")
                assert [x["tag"] for x in warm] == [
                    "warm/livefixture.Control.scalar(I)I", "warm/livefixture.Control.array([II)I",
                    "warm/livefixture.Control.floating(F)I"]
                for row in warm:
                    check_stats(row)
                    assert row["status"] == "ok" and row["arm"] == arm and row["stats"]["checks"] > 0
                    warmup_outcomes[arm][row["stats"]["stop_reason"]] += 1
                phase_order = [row[0] for row in evs if row[0] in {
                    "driver_start","index_start","index_end","warmup_start","warmup_end","identity_end","batch_start"}]
                expected_phase_order = ["driver_start","index_start","index_end","warmup_start","warmup_end","identity_end","batch_start"]
                assert phase_order == expected_phase_order[:len(phase_order)]
            began = {line[-1]:float(line[2]) for line in evs if line[0]=="method_start"}
            ended = {line[-1]:float(line[2]) for line in evs if line[0]=="method_end"}
            assert list(began) == job["tags"][:len(began)] and list(ended) == job["tags"][:len(ended)]
            rows = lines(p/"results.jsonl")
            assert [x["tag"] for x in rows] == job["tags"][:len(rows)]
            if status:
                assert status["emitted"] == [r["tag"] for r in rows] and case_end["emitted"] == len(rows)
                if status["returncode"] == 0 and verified:
                    assert len(rows) == len(job["tags"]) and len(ended) == len(rows)
                    assert all(any(e[0]==k for e in evs) for k in ("batch_start","batch_end","driver_end"))
            for row in rows:
                assert row["arm"] == arm and row["tag"] in began
                check_stats(row)
                if row["tag"] in ended:
                    assert row["wall_ms"] <= ended[row["tag"]] - began[row["tag"]] + .01
                if verified:
                    totals[arm].update({k:row["stats"][k] for k in ("checks","checks_assume","sat","unsat","unknown","model_calls")})
            by_tag = {r["tag"]:r for r in rows}
            for tag in job["tags"]:
                row = by_tag.get(tag)
                outcome = get_status_vars(row,status,verified,tag in began)
                counts[arm][outcome] += 1
                if outcome in ("budget_complete","method_timeout"):
                    st=row["stats"]
                    stop_counts[arm][st["stop_reason"]] += 1
                    coverage[arm][job["id"],tag] = dict(project=job["project"],
                        coverage_pct=st["coverage_pct"], branch_edges=len(st["branch_edges"]),
                        completed_paths=st["completed_paths"], states=row["states"],
                        **{k:st[k] for k in ("steps","forking_steps","terminated","returned_paths")})
                wall[arm][job["project"]].append(row["wall_ms"] if outcome=="budget_complete" else None)
            for f in p.glob("*"):
                if f.is_file():
                    raw_hashes[str(f.relative_to(WORK))] = sha(f)
        if "cvc5-default" in verified_here and "cvc5-none" in verified_here:
            verify_option_delta(verified_here["cvc5-default"],verified_here["cvc5-none"])
    assert len(opts)==1
    effective = opts.pop()
    for phrase in ("FORK_DEPTH","randomSeed=0","coverageZone=TRANSITIVE","stepLimit=4000",
                   "timeout=6s","solverTimeout=1.6s","runSolverInAnotherProcess=false","throwExceptionOnStepFailure=true"):
        assert phrase in effective, (phrase,effective)
    for core, lane in intervals.items():
        series=sorted(lane)
        assert all(end <= start for (_,end),(start,_) in zip(series,series[1:]))
        topology = Path(f"/sys/devices/system/cpu/cpu{core}/topology/thread_siblings_list")
        assert topology.read_text().strip() == str(core)
    for a in MODES:
        reported=metrics["arms"][a]
        assert dict(counts[a]) == reported["statuses"]
        assert dict(stop_counts[a]) == reported["stop_reasons"]
        assert sum(counts[a].values()) == reported["intended"] == 480
        for k,n in totals[a].items():
            assert n == reported["solver"][k]
        cv=list(coverage[a].values())
        for metric,expected in metrics["arms"][a]["coverage_metrics"].items():
            assert expected == sum(r[metric] for r in cv)
        observed_pct=math.fsum(r["coverage_pct"] for r in cv)
        assert same(observed_pct/len(cv),reported["coverage_pp"])
        assert same(observed_pct/480,reported["coverage_missing_zero"])
        for k,n in jvm_outcomes[a].items():
            assert n == reported["jvm"][k]
        for penalty in (1,2,3):
            scores=[(8000*penalty if x is None else x) for sub in wall[a].values() for x in sub]
            assert same(sum(scores)/len(scores), reported["penalties"][str(penalty)])

    rng = random.Random(504141)
    sample = [Counter(rng.choices(projects,k=len(projects))) for _ in range(10000)]
    independent, independent_coverage = {}, {}
    for a,b in [("cvc5-none","cvc5-default"),("cvc5-default","z3"),("cvc5-none","z3")]:
        cmp = f"{a}/{b}"
        independent[cmp] = {}
        for p in (1,2,3):
            scores = {side:{g:math.fsum(8000*p if x is None else x for x in wall[side][g]) for g in projects}
                      for side in (a,b)}
            point = math.fsum(scores[a].values())/math.fsum(scores[b].values())
            draw = sorted(math.fsum(n*scores[a][g] for g,n in weights.items()) /
                          math.fsum(n*scores[b][g] for g,n in weights.items()) for weights in sample)
            ci = [quantile(draw,q) for q in [.025,.975]]
            expected = metrics["comparisons"][cmp]["par"][str(p)]
            assert same(point,expected["value"]) and all(same(x,y) for x,y in zip(ci,expected["ci"]))
            independent[cmp][str(p)] = {"ratio":point,"ci95":ci}
        pairs = [(coverage[a][tag],coverage[b][tag]) for tag in sorted(coverage[a].keys() & coverage[b].keys())]
        tested = {"coverage_pct":"coverage_pp","branch_edges":"branch_edges","completed_paths":"completed_paths"}
        independent_coverage[cmp] = {}
        for field,label in tested.items():
            grouped=defaultdict(lambda:[0.0,0.0,0])
            for x,y in pairs:
                assert x["project"] == y["project"]
                g=grouped[x["project"]]
                g[0]+=x[field]
                g[1]+=y[field]
                g[2]+=1
            def cov_score(weights):
                n,d,k=(math.fsum(w*grouped[g][i] for g,w in weights.items()) for i in range(3))
                if field=="coverage_pct":
                    return (n-d)/k if k else None
                return n/d if d else None
            fitted_values=sorted(x for weights in sample if (x:=cov_score(weights)) is not None)
            ratio_or_diff=cov_score(Counter(projects))
            band=[quantile(fitted_values,q) for q in (.025,.975)]
            stored=metrics["comparisons"][cmp][label]
            assert stored["n"]==len(pairs)
            assert same(ratio_or_diff,stored["value"]) and all(same(x,y) for x,y in zip(band,stored["ci"]))
            independent_coverage[cmp][label]={"value":ratio_or_diff,"ci95":band,"n":len(pairs)}
    certificate={
        "verified_at_msk":datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "ok":True,"project_clusters":len(projects),"methods":sum(map(len,tags.values())),
        "planned_blocks":len(schedule["jobs"]),"recorded_jvms":len(case_events),
        "method_attempts":sum(sum(scores.values()) for scores in counts.values()),
        "native_versions_verified":identity,"independent_penalty_recompute":independent,
        "independent_coverage_recompute":independent_coverage,
        "outcomes":counts,"jvms":jvm_outcomes,"warmup_outcomes":warmup_outcomes,"manifest_sha256":sha(INPUT/"manifest.json"),
        "schedule_sha256":sha(INPUT/"schedule.json"),"timing_log_sha256":sha(RAW/"supervisor.jsonl"),
        "raw_file_sha256":raw_hashes,"metrics_sha256":sha(RAW/"metrics.json"),
    }
    tmp=RAW/"VERIFY.tmp"
    tmp.write_text(json.dumps(certificate,ensure_ascii=False,indent=2)+"\n")
    tmp.replace(RAW/"VERIFY.json")
    print("VERIFIED",certificate["recorded_jvms"],"JVMs,",certificate["method_attempts"],
          "intended methods,",len(raw_hashes),"raw hashes")

if __name__ == "__main__":
    main()
