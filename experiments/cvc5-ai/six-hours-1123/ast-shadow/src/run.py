"""Evaluate each live KSMT prefix in the same context and audit matched exact answers."""
from collections import Counter, defaultdict
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
from measure import ACQUISITION_END, attest, base, measure
from analyze_power import cluster_ratio
from recover import host_sample, manifest

PROTOCOL = TOP / "protocols/17-live-ast-shadow.md"
ARMS = {"A": {}, "B": {}, "gauss": {"bv-gauss-elim": "true"},
        "bitwise": {"bitwise-eq": "true"}, "no-static": {"static-learning": "false"},
        "array-index": {"arrays-eager-index": "false"}, "z3": {}}
CAP = 130
KNOWN = {"SAT", "UNSAT"}


def build(cpu=14):
    prepare()
    spec = importlib.util.spec_from_file_location("trace_build", TOP / "trace-cause/src/run.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.ROOT = ROOT
    return helper.compile_overlay(cpu, (base.BUILD/"overlay", base.OLD/"build/client"))


def freeze():
    path = ROOT / "inputs/schedule.json"
    kernel_file = TOP / "kernels/inputs/schedule.json"
    traces_file = TOP / "trace-cause/inputs/schedule.json"
    sources = [base.sha(x) for x in (PROTOCOL, kernel_file, traces_file)]
    if not path.exists():
        kernels = base.read(kernel_file)
        fixture = TOP / "kernels/build/kernels.jar"
        assert base.sha(fixture) == kernels["fixture"]
        assets = [{"path": str(fixture), "sha256": kernels["fixture"]}]
        panel = {"java-"+f: dict(tag=v[sorted(v)[0]], files=assets, kind="java")
                 for f, v in kernels["cases"].items()}
        priority = {"full-equal": 0, "count-equal": 1, "work-different": 2}
        for p, x in base.read(traces_file)["projects"].items():
            first = min(x["tags"], key=lambda tag: (priority[x["tags"][tag]], tag))
            panel[p] = dict(tag=first, files=x["assets"], kind="library")
        rng = random.Random(23111701)
        schedule = []
        for i, (name, program) in enumerate(panel.items()):
            labels = list(ARMS)
            rng.shuffle(labels)
            for rep, shift in enumerate(rng.sample(range(len(labels)), 4)):
                order = labels[shift:] + labels[:shift]
                schedule.append(dict(id=f"{name}-r{rep+1}", project=name, tags=[program["tag"]],
                                     order=order, core=base.CPUS[(i+rep)%4], seed=(0,71)[rep%2], trace=False))
        rng.shuffle(schedule)
        base.write(path, dict(at=base.now(), sources=sources, panel=panel, schedule=schedule, arms=ARMS))
    plan = base.read(path)
    assert (plan["sources"], plan["arms"]) == (sources, ARMS)
    return plan


def raw_log(path, job):
    try:
        lines = (path / "shadow.tsv").read_text().splitlines()
    except FileNotFoundError:
        lines = []
    groups = []
    tag = job["tags"][0]
    for line in lines:
        kind, *v = line.split("\t")
        if kind == "BEGIN":
            assert v[0] == tag
            groups.append(dict(operations=int(v[1]), checks=int(v[2]), capped=v[3]=="true",
                               ended=False, options={}, answers=defaultdict(list), versions={}, errors={}, done={}, shapes=[]))
        else:
            cur = groups[-1]
            if kind == "END":
                assert v[0] == tag
                cur["ended"] = True
            elif kind == "OPTIONS":
                arm, phase, version, options = v
                settings = dict(x.split("=",1) for x in options.split(","))
                expected = {**ARMS[arm], "seed":"0", "incremental":"true", "produce-models":"true",
                            "produce-unsat-cores":"true"}
                if phase == "after":
                    expected["tlimit-per"] = "250"
                assert version == "1.4.1" and all(settings[k]==h for k,h in expected.items()), (path, line)
                cur["options"][arm+"/"+phase] = settings
                cur["versions"][arm] = version
            elif kind == "VERSION":
                assert v == ["z3", "4.13.0.0"]
                cur["versions"]["z3"] = v[1]
            elif kind == "SHAPE":
                i, theories, n, mul, div, limit, other = v
                assert int(i) == len(cur["shapes"]) + 1
                cur["shapes"].append(dict(theories=theories, nodes=int(n), mul=int(mul), div=int(div),
                                           truncated=limit=="true", other_nodes=int(other)))
            elif kind == "CHECK":
                arm, i, op, reference, result, cpu, wall, explanation = v
                q = cur["answers"][arm]
                assert int(i) == len(q)+1 and 1 <= int(i) <= cur["checks"] <= 32
                assert op in {"CHECK","ASSUME"} and reference in KNOWN|{"UNKNOWN"} and result in KNOWN|{"UNKNOWN"}
                q.append(dict(i=int(i), op=op, original=reference, status=result,
                              cpu_ms=int(cpu)/1e6, wall_ms=int(wall)/1e6, reason=explanation))
            elif kind == "DONE":
                arm, n, skips = v
                assert int(n) == cur["checks"] == len(cur["answers"][arm])
                cur["done"][arm] = int(skips)
            else:
                assert kind == "ERROR", line
                cur["errors"][v[0]] = v[1]
    assert all(len(g["shapes"]) == g["checks"] for g in groups if g["ended"])
    return groups


def pairs(blocks, projects):
    rows = {name: [] for name in ARMS if name not in ("A", "z3")}
    checks = {name: Counter() for name in (*ARMS,"original")}
    theories={kind:Counter() for kind in ("java","library")}
    for b in blocks:
        job = b["job"]
        project = job["project"]
        for s in b["prefixes"]:
            theories[projects[project]["kind"]].update(q["theories"]+" (cap)" if q["truncated"] else q["theories"]
                                                      for q in s["shapes"])
            for arm in ARMS:
                checks[arm].update(sessions=1,done=arm in s["done"],errors=arm in s["errors"],
                                   skipped_models_or_cores=s["done"].get(arm,0),
                                   expected=s["checks"], missing=s["checks"]-len(s["answers"].get(arm, [])))
            resolved = s["answers"].get("z3", [])
            shared = s["answers"].get("A", [])
            for arm in ARMS:
                checks[arm].update(r["status"] for r in s["answers"].get(arm, []))
            for oracle in resolved:
                checks["original"].update([oracle["original"]])
                if oracle["status"] not in KNOWN:
                    continue
                i = oracle["i"]-1
                expected = oracle["status"]
                checks["original"].update(oracle_known=1,correct=oracle["original"] == expected,
                                           wrong=oracle["original"] in KNOWN-{expected})
                for arm in ARMS:
                    entries = s["answers"].get(arm, [])
                    if i >= len(entries):
                        continue
                    r = entries[i]
                    assert r["op"] == oracle["op"] and r["original"] == oracle["original"]
                    checks[arm].update(oracle_known=1,correct=r["status"]==expected,wrong=r["status"] in KNOWN-{expected})
                    if arm in rows and i < len(shared) and r["status"] == shared[i]["status"] == expected:
                        rows[arm].append(dict(project=project,job=job["id"],i=i+1,
                             cpu=[r["cpu_ms"],shared[i]["cpu_ms"]], wall=[r["wall_ms"],shared[i]["wall_ms"]]))
    ratios = {}
    for arm, values in rows.items():
        res = {}
        for kind in ("all", "java", "library"):
            data = [r for r in values if kind == "all" or projects[r["project"]]["kind"]==kind]
            res[kind] = dict(n=len(data), **{k: cluster_ratio(data,k) for k in ("cpu","wall")})
        ratios[arm]=res
    return dict(checks=checks,ratios=ratios,theories=theories)


def main():
    base.setup_limits()
    plan = freeze()
    runtime = build()
    def lane(core):
        output = []
        for job in plan["schedule"]:
            if job["core"] != core:
                continue
            raw = ROOT / "raw" / job["id"] / "A"
            certificate = ROOT / "evidence" / (job["id"]+".json")
            if certificate.is_file():
                saved = base.read(certificate)
                assert saved["arms"]["A"]["attestation"]["digest"] == manifest(raw)
                output.append(saved)
                continue
            if time.time()+CAP+10 >= ACQUISITION_END:
                break
            if raw.exists() and not (raw / "status.json").is_file():
                raw.parent.rename(raw.parent.with_name(raw.parent.name+f"-interrupted-{int(time.time())}"))
            files = plan["panel"][job["project"]]["files"]
            assert all(base.sha(f["path"]) == f["sha256"] for f in files)
            if not raw.exists():
                base.ensure_disk()
                measure(raw, job, "fixed", "A", os.pathsep.join(f["path"] for f in files), runtime=runtime,
                        watchdog_s=CAP, extra_env={"SMT_SHADOW_ORDER": ",".join(job["order"])})
            row = dict(job=job, arms={"A": {"attestation": attest(raw,job)}}, prefixes=raw_log(raw,job))
            base.write(certificate,row)
            output.append(row)
            print("typed shadow",job["id"],base.now(),len(row["prefixes"]),flush=True)
        return output
    with ThreadPoolExecutor(4) as ex:
        blocks=[b for lane_blocks in ex.map(lane,base.CPUS) for b in lane_blocks]
    if not blocks:
        return
    st=[b["arms"]["A"]["attestation"] for b in blocks]
    base.write(ROOT/"evidence/summary.json",dict(start=min(r["start"] for r in st),end=max(r["end"] for r in st),
               blocks=len(blocks),jvms=len(st),planned=len(plan["schedule"]),jvm_failed=sum(r["jvm_failed"] for r in st),
               programs=len({b["job"]["project"] for b in blocks}), options=ARMS,host=host_sample(),
               hook_protocol=base.sha(TOP/"protocols/17b-shadow-hook.md"),
               **pairs(blocks,plan["panel"])))


if __name__ == "__main__":
    main()
