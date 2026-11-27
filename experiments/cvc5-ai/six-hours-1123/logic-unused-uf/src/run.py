"""Widen declared logic to include unused uninterpreted functions."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
TOP = ROOT.parent
SPEC = spec_from_file_location("logic_study", TOP / "logic-study/src/run.py")
impl = module_from_spec(SPEC)
SPEC.loader.exec_module(impl)
SOURCE = TOP / "native"
PROTOCOL = TOP / "protocols/31-logic-unused-uf.md"
WIDER = {"(set-logic QF_BV)": "(set-logic QF_UFBV)", "(set-logic QF_ABV)": "(set-logic QF_AUFBV)"}
base = impl.base


def freeze():
    seal = ROOT / "inputs/manifest.json"
    first = TOP / "logic-study/inputs/manifest.json"
    hashes = {"protocol": base.sha(PROTOCOL), "original_plan": base.sha(first)}
    if not seal.is_file():
        programs = base.read(first)["programs"]
        rng = random.Random(23113101)
        jobs = []
        for i, (key, p) in enumerate(programs.items()):
            origin = SOURCE / p["path"]
            assert base.sha(origin) == p["sha256"]
            head, sep, tail = origin.read_bytes().partition(b"\n")
            assert head.decode() == p["logic"] and sep
            for label, declaration in (("all", "(set-logic ALL)"), ("uf", WIDER[p["logic"]])):
                where = ROOT / "inputs/streams" / (key + "-" + label + ".smt2")
                where.parent.mkdir(parents=True, exist_ok=True)
                where.write_bytes(declaration.encode() + b"\n" + tail)
                p[label] = str(where.relative_to(ROOT))
                p[label + "_sha"] = base.sha(where)
                if label == "uf":
                    p["uf_logic"] = declaration
            order = ["A", "B", "C", "U"]
            rng.shuffle(order)
            for rep in range(3):
                jobs.append(dict(id=key + f"-r{rep + 1}", program=key, core=base.CPUS[(i + rep) % 4],
                                 order=order[rep:] + order[:rep]))
        rng.shuffle(jobs)
        base.write(seal, dict(at=base.now(), hashes=hashes, programs=programs, jobs=jobs))
    plan = base.read(seal)
    assert plan["hashes"] == hashes
    return plan


if __name__ == "__main__":
    impl.ROOT = ROOT
    impl.freeze = freeze
    impl.main()
