"""Run paired USVM checks with observed and experimentally stabilized assertion order."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import importlib.util
import os
import random
import sys
import time

from prepare import prepare

ROOT = Path(__file__).resolve().parents[1]
TOP = ROOT.parent
sys.path[:0] = [str(TOP / "stable/src"), str(TOP / "followup/src")]
from measure import ACQUISITION_END, attest, base, measure, metrics
from analyze_power import cluster_ratio
from recover import host_sample, manifest

PROTOCOL = TOP / "protocols/16-assertion-order.md"
HISTORY = TOP / "trace-cause/inputs/schedule.json"
RUNTIME_CAP = 115


def load_trace_driver():
    spec = importlib.util.spec_from_file_location("trace_build", TOP / "trace-cause/src/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compile_overlay(cpu=14):
    prepare()
    helper = load_trace_driver()
    helper.ROOT = ROOT
    return helper.compile_overlay(cpu)


def freeze():
    path = ROOT / "inputs/schedule.json"
    if not path.is_file():
        previous = base.read(HISTORY)
        rng = random.Random(23111601)
        jobs = []
        for parent in previous["assigned"]:
            order = ["A", "B", "O", "Q"]
            rng.shuffle(order)
            jobs.append({**parent, "order": order})
        rng.shuffle(jobs)
        spec = dict(frozen=base.now(), protocol=base.sha(PROTOCOL), parent=base.sha(HISTORY),
                    projects=previous["projects"], jobs=jobs)
        base.write(path, spec)
    plan = base.read(path)
    assert plan["protocol"] == base.sha(PROTOCOL) and plan["parent"] == base.sha(HISTORY)
    return plan


def prefix_n(a, b):
    for i, (left, right) in enumerate(zip(a, b)):
        if left != right:
            return i
    return min(len(a), len(b))


def stats(arm, tag):
    row = arm["rows"].get(tag)
    if row and row["status"] == "ok" and row["stats"]["checks"]:
        return row["stats"]
    return None


def compare(blocks, labels):
    first, second = labels
    totals = Counter()
    projects = {}
    values = []
    for block in blocks:
        job = block["job"]
        a, b = (block["arms"][x] for x in labels)
        for tag in job["tags"]:
            project = projects.setdefault(job["project"], Counter())
            left, right = stats(a, tag), stats(b, tag)
            if left is None or right is None:
                project.update(["no_pair"])
                continue
            for x, label in ((left, first), (right, second)):
                assert x["sort_input"] == (label in ("O", "Q"))
                assert len(x["unordered_query_prefix_sha256"]) == min(512, x["checks"])
                assert 0 <= x["reordered_lists"] <= x["sorted_lists"]
            same_order = left["query_sha256"] == right["query_sha256"]
            same_multiset = left["unordered_query_sha256"] == right["unordered_query_sha256"]
            same_path = left["path_sha256"] == right["path_sha256"]
            exact_prefix = prefix_n(left["check_prefix_sha256"], right["check_prefix_sha256"])
            assert not same_order or same_multiset
            project.update(dict(pairs=1, same_order=same_order, same_multiset=same_multiset,
                                same_path=same_path, same_full=left["work_sha256"] == right["work_sha256"],
                                same_path_order_drift=same_path and not same_order,
                                reorder_explains=same_path and not same_order and same_multiset,
                                ordered_prefix=prefix_n(left["query_prefix_sha256"], right["query_prefix_sha256"]),
                                multiset_prefix=prefix_n(left["unordered_query_prefix_sha256"],right["unordered_query_prefix_sha256"]),
                                exact_prefix=exact_prefix, compared_prefix=min(512,left["checks"],right["checks"])))
            values.append(dict(project=job["project"], tag=tag, block=job["id"], exact_prefix=exact_prefix,
              check_prefix_ms=[sum(s["check_ms_list"][:exact_prefix]) for s in (right,left)],
              sorted_lists=[left["sorted_lists"],right["sorted_lists"]],
              reordered_lists=[left["reordered_lists"],right["reordered_lists"]],
              sort_ms=[left["sort_ms"],right["sort_ms"]]))
    for p in projects.values():
        totals.update(p)
    prefix_values = [row for row in values if row["exact_prefix"]]
    clock = cluster_ratio(prefix_values,"check_prefix_ms") if any(row["check_prefix_ms"][1] for row in prefix_values) else None
    return dict(totals=dict(totals), projects={k:dict(v) for k,v in projects.items()},
                prefixes=clock, values=values)


def main():
    base.setup_limits()
    plan = freeze()
    compiled = compile_overlay()
    for folder in (ROOT / "raw", ROOT / "evidence"):
        folder.mkdir(exist_ok=True)

    def lane(core):
        out = []
        for job in plan["jobs"]:
            if job["core"] != core:
                continue
            raw = ROOT / "raw" / job["id"]
            file = ROOT / "evidence" / (job["id"] + ".json")
            if file.is_file():
                saved=base.read(file)
                assert all(r["attestation"]["digest"] == manifest(raw / name) for name,r in saved["arms"].items())
                out.append(saved)
                continue
            if time.time() + len(job["order"]) * (RUNTIME_CAP + 10) >= ACQUISITION_END:
                break
            jars = plan["projects"][job["project"]]["assets"]
            assert all(base.sha(a["path"]) == a["sha256"] for a in jars)
            classpath = os.pathsep.join(a["path"] for a in jars)
            arms = {}
            if raw.exists() and not all((raw / arm / "status.json").is_file() for arm in job["order"]):
                raw.rename(raw.with_name(raw.name + f"-interrupted-{int(time.time())}"))
            for arm in job["order"]:
                where = raw / arm
                if not where.exists():
                    base.ensure_disk()
                    measure(where, job, "fixed", arm, classpath, runtime=compiled, watchdog_s=RUNTIME_CAP,
                            extra_env={"SMT_SORT_INPUT": "1" if arm in ("O","Q") else "0"})
                arms[arm] = dict(attestation=attest(where, job), rows=metrics.rows_from(where))
            block = dict(job=job, arms=arms)
            base.write(file, block)
            out.append(block)
            print("assertion order", job["id"], base.now(), flush=True)
        return out

    with ThreadPoolExecutor(4) as pool:
        blocks = [b for lane_output in pool.map(lane, base.CPUS) for b in lane_output]
    if not blocks:
        return
    clocks = [r["attestation"] for b in blocks for r in b["arms"].values()]
    pairs = {right+"/"+left:compare(blocks, (left,right)) for left,right in (("A","B"),("O","Q"),("A","O"))}
    coverage = {arm:metrics.comparison([b["arms"][arm]["attestation"] for b in blocks],
                                    [b["arms"]["A"]["attestation"] for b in blocks])
                for arm in ("B","O","Q")}
    base.write(ROOT / "evidence/summary.json", dict(start=min(r["start"] for r in clocks),
      end=max(r["end"] for r in clocks), host=host_sample(), jvms=len(clocks), blocks=len(blocks),
      planned=len(plan["jobs"]), comparisons=pairs, coverage=coverage))


if __name__ == "__main__":
    main()
