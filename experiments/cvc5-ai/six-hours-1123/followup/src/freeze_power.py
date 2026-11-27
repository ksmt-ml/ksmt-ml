from pathlib import Path
import hashlib
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "recovery/scripts"))
import base

PROJECTS = ("automaton","re2j","RoaringBitmap","commons-pool2","lz4-java","generics-resolver")
SALT = "2026-11-23-usvm-aa-250us-v1"

def rank(item):
    return hashlib.sha256(f"{SALT}\n{item['tag']}".encode()).digest()

def main():
    out = ROOT/"inputs/power.json"
    if out.exists():
        return
    old = base.read(base.OLD/"inputs/manifest.json")
    previous = base.read(base.ROOT/"inputs/manifest.json")
    seen = {m["class"].split("$")[0] for p in old["projects"] for m in p["methods"]}
    seen.update(m["class"].split("$")[0] for split in previous["splits"].values()
                for p in split for m in p["methods"])
    inputs = {name:digest for name,digest in previous["earlier_files_rechecked"].items()
              if Path(name).is_file()}
    history = []
    for name,digest in inputs.items():
        assert base.sha(name) == digest
        history.append(Path(name).read_text(errors="replace"))
    projects = {}
    rng = random.Random(2311141)
    jobs = []
    for i, name in enumerate(PROJECTS):
        p = next(p for p in old["projects"] if p["project"] == name)
        files = [p["jar"], *p["dependencies"]]
        for file in files:
            assert base.sha(file["path"]) == file["sha256"]
        methods = []
        for line in (base.OLD/"inputs"/f"{name}-eligible.tsv").read_text().splitlines():
            cls,method,sig,size,branches = line.split("\t")
            outer = cls.split("$")[0]
            if outer in seen or any(outer in text or outer.replace(".", "/") in text for text in history):
                continue
            methods.append(dict(tag=f"{name}/{cls}.{method}{sig}",outer=outer,
                                bytecode=int(size),branches=int(branches)))
        picks = []
        assigned = set()
        for hard in (False,True):
            for method in sorted(methods,key=rank):
                if (method["branches"] >= 3) == hard and method["outer"] not in assigned:
                    picks.append(method)
                    assigned.add(method["outer"])
                    if len(picks) in (3,6):
                        break
        for method in sorted(methods,key=rank):
            if len(picks) >= 6:
                break
            if method["outer"] not in assigned:
                picks.append(method)
                assigned.add(method["outer"])
        if len(picks) < 3:
            print("Too few independent source classes", name, len(picks), flush=True)
            continue
        projects[name] = dict(files=files,methods=picks,eligible_classes=len({m["outer"] for m in methods}))
        arms = [(budget, arm) for budget in ("wall","fixed") for arm in ("A","B","P")]
        rng.shuffle(arms)
        rotations = [0, 2, 3, 5]
        rng.shuffle(rotations)
        for rep, shift in enumerate(rotations):
            tags = [m["tag"] for m in picks]
            rng.shuffle(tags)
            jobs.append(dict(id=f"{name}-power-r{rep+1}",project=name,rep=rep+1,seed=(0,71)[rep%2],
                      core=base.CPUS[(i+rep)%4],tags=tags,trace=rep>=2,order=arms[shift:]+arms[:shift]))
    rng.shuffle(jobs)
    protocol = ROOT.parent/"protocols/01-power.md"
    assert len(projects) >= 4, projects.keys()
    base.write(out, dict(frozen_msk=base.now(),salt=SALT,seed=2311141,projects=projects,jobs=jobs,
                        preregistration_sha256=base.sha(protocol),prior_hashes=inputs))

if __name__ == "__main__":
    main()
