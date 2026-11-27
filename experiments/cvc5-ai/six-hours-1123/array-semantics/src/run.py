"""Verify actual cvc5 options, then check solution classes against concrete Java."""
from collections import Counter
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location
import random
import sys

from generate import main as generate

ROOT = Path(__file__).resolve().parents[1]
TOP = ROOT.parent
sys.path[:0] = [str(TOP / "recovery/scripts"), str(TOP / "followup/src")]
import base
from analyze_power import cluster_ratio
from recover import manifest

PROTOCOL = TOP / "protocols/18-array-semantics.md"
ARMS = {"A":{}, "B":{}, "E":{"arrays-exp":"true"},
        "EI":{"arrays-exp":"true","arrays-eager-index":"false"},
        "ES":{"arrays-exp":"true","static-learning":"false"},"z3":{}}


def drivers():
    spec = spec_from_file_location("kernel_driver", TOP / "kernels/src/run.py")
    kernel = module_from_spec(spec)
    spec.loader.exec_module(kernel)
    kernel.ROOT = ROOT
    return kernel, kernel.reuse()


def freeze(jar, options):
    path = ROOT / "inputs/schedule.json"
    if not path.is_file():
        expected = {}
        cases = {}
        oracle = base.read(ROOT / "evidence/build/runtime.json")["concrete"]
        for line in oracle.splitlines():
            family, cls, width, k, categories = line.split("\t")
            tag = f"kernel/examples.kernels.{cls}.run(II)I"
            cases.setdefault(family,{})[cls] = tag
            expected[tag] = sorted(map(int,categories.split(",")))
        rng = random.Random(23111810)
        jobs = []
        for i,(family, programs) in enumerate(cases.items()):
            arms = list(options)
            rng.shuffle(arms)
            shifts = rng.sample(range(len(arms)),4)
            for rep, k in enumerate(shifts):
                tags = list(programs.values())
                rng.shuffle(tags)
                jobs.append(dict(project=family,id=f"{family}-r{rep+1}",order=arms[k:]+arms[:k],
                                 core=base.CPUS[(i+rep)%4],seed=(0,71)[rep%2],tags=tags,expected=[]))
        rng.shuffle(jobs)
        base.write(path,dict(at=base.now(),protocol=base.sha(PROTOCOL),fixture=base.sha(jar),arms=options,
                           source=manifest(ROOT/"src/programs"),cases=cases,expected=expected,schedule=jobs))
    frozen = base.read(path)
    assert frozen["protocol"] == base.sha(PROTOCOL) and frozen["fixture"] == base.sha(jar)
    assert frozen["source"] == manifest(ROOT/"src/programs") and frozen["arms"] == options
    return frozen


def summarize(plan):
    cases = [base.read(p) for p in (ROOT/"evidence").glob("*-r*.json")]
    observations = {a: [] for a in plan["arms"]}
    families = {f: {a: Counter() for a in plan["arms"]} for f in plan["cases"]}
    for trial in cases:
        project = trial["job"]["project"]
        for a,value in trial["runs"].items():
            for name,r in value["cells"].items():
                complete=r["witnessed"] == r["expected"]
                expected=r["expected"]
                status=families[project][a]
                reasons=r["unknown_reasons"]
                entries=sum(n for k,n in reasons.items() if "STORE_ALL" in k)
                status.update(n=1,complete=complete,correct=len(r["witnessed"]),expected=len(expected),
                   wrong=r["wrong"],witness_errors=r["witness_errors"],step_errors=r["step_errors"],
                   timeout=r["solver_timeouts"],other_unknown=r["other_unknown"],store_all=entries,
                   jvm_failed=value["jvm_failed"],par2_ms=r["par2_ms"],checks=r["checks"],paths=r["paths"])
                compare_to="E" if a in ("EI","ES") and "E" in trial["runs"] else "A"
                d=trial["runs"][compare_to]["cells"][name]
                work=complete and d["witnessed"]==d["expected"] and not any(
                 x[k] for x in (r,d) for k in ("wrong","witness_errors","solver_unknown","step_errors","failed"))
                work=work and all(r[k]==d[k] for k in ("paths","edges","steps","checks","coverage"))
                observations[a].append(dict(project=project,paired=work,control=compare_to,
                           check=[r["check_ms"],d["check_ms"]],wall=[r["observed_wall_ms"],d["observed_wall_ms"]],
                           par2=[r["par2_ms"],d["par2_ms"]]))
    tables={}
    for a,rows in observations.items():
        partner=next(iter(rows),{}).get("control")
        work=[r for r in rows if r["paired"]]
        tables[a]=dict(control=partner,n=len(rows),matched=len(work),par2=cluster_ratio(rows,"par2"),
                       matched_check=cluster_ratio(work,"check"),matched_wall=cluster_ratio(work,"wall"))
    return dict(families=families,contrasts=tables,planned=len(plan["schedule"]),options=plan["arms"])


def main():
    base.setup_limits()
    kernel, witness = drivers()
    generate()
    bundle = kernel.compile_fixture()
    kernel.ARMS = {k:v for k,v in ARMS.items() if k!="z3"}
    certified = kernel.contracts()
    options = {k:v for k,v in ARMS.items() if k in {"B","z3"} or certified[k]["ok"]}
    plan = freeze(bundle[0],options)
    old_audit = witness.audit

    def audit(path,job):
        r=old_audit(path,job)
        for cell in r["cells"].values():
            cell["expected"]=plan["expected"][cell["tag"]]
        return r

    witness.ROOT=ROOT
    witness.ARMS=options
    witness.CASES=plan["cases"]
    witness.prep=lambda: bundle
    witness.freeze=lambda jar: plan
    witness.require_preregistered=lambda expected: expected==base.sha(PROTOCOL)
    witness.audit=audit
    witness.main()
    out=ROOT/"evidence/summary.json"
    d=base.read(out)
    d.update(summarize(plan))
    base.write(out,d)


if __name__ == "__main__":
    main()
