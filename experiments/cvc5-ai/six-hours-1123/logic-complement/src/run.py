"""Replicate the native logic comparison on its disjoint program complement."""
from collections import defaultdict
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
TOP = ROOT.parent
SPEC = spec_from_file_location("logic_study", TOP / "logic-study/src/run.py")
impl = module_from_spec(SPEC)
SPEC.loader.exec_module(impl)
SOURCE = TOP / "native"
PROTOCOL = TOP / "protocols/30-logic-complement.md"
base = impl.base


def freeze():
    seal = ROOT / "inputs/manifest.json"
    source = SOURCE / "inputs/manifest.json"
    oracle = SOURCE / "evidence/oracle.json"
    discovery = TOP / "logic-study/inputs/manifest.json"
    hashes = {p.name: base.sha(p) for p in (PROTOCOL, source, oracle)}
    hashes["discovery"] = base.sha(discovery)
    if not seal.is_file():
        confirmed = {p["id"] for p in base.read(oracle) if p["verified"]}
        used = set(base.read(discovery)["programs"])
        programs = {}
        groups = defaultdict(list)
        for p in sorted(base.read(source)["programs"], key=lambda p: p["id"]):
            if p["id"] not in confirmed - used:
                continue
            origin = SOURCE / p["path"]
            assert base.sha(origin) == p["sha256"]
            head, sep, tail = origin.read_bytes().partition(b"\n")
            assert head in (b"(set-logic QF_BV)", b"(set-logic QF_ABV)") and sep
            where = ROOT / "inputs/streams" / (p["id"] + ".smt2")
            where.parent.mkdir(parents=True, exist_ok=True)
            where.write_bytes(b"(set-logic ALL)\n" + tail)
            programs[p["id"]] = dict(p, all=str(where.relative_to(ROOT)), all_sha=base.sha(where), logic=head.decode())
            groups[p["family"]].append(p["id"])
        rng = random.Random(23113001)
        jobs = []
        for i in range(max(map(len, groups.values()))):
            for j, family in enumerate(sorted(groups)):
                if i >= len(groups[family]):
                    continue
                key = groups[family][i]
                order = ["A", "B", "C"]
                rng.shuffle(order)
                for rep in range(2):
                    jobs.append(dict(id=key + f"-r{rep + 1}", program=key, core=base.CPUS[(i + j + rep) % 4],
                                     order=order[rep:] + order[:rep]))
        assert set(programs) == confirmed - used
        base.write(seal, dict(at=base.now(), hashes=hashes, programs=programs, jobs=jobs))
    plan = base.read(seal)
    assert plan["hashes"] == hashes
    return plan


if __name__ == "__main__":
    impl.ROOT = ROOT
    impl.freeze = freeze
    impl.main()
