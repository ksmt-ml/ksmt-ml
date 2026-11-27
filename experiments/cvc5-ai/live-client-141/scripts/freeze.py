import itertools
from collections import Counter
import random
import re
import subprocess

from common import *

MAVEN = Path.home() / ".m2/repository"
# Only project source jar is scanned. Extra classpath jars supply dependencies.
SOURCES = [
    ("re2j", "com/google/re2j/re2j/1.6", "com.google.re2j.", ()),
    ("automaton", "dk/brics/automaton/automaton/1.11-8", "dk.brics.automaton.", ()),
    ("jsoup", "org/jsoup/jsoup/1.17.2", "org.jsoup.", ()),
    ("commons-validator", "commons-validator/commons-validator/1.7", "org.apache.commons.validator.routines.", (
        "commons-beanutils/commons-beanutils/1.9.4", "commons-digester/commons-digester/2.1",
        "commons-logging/commons-logging/1.2", "commons-collections/commons-collections/3.2.2")),
    ("packageurl-java", "com/github/package-url/packageurl-java/1.4.1", "com.github.packageurl.", ()),
    ("hppc", "com/carrotsearch/hppc/0.8.1", "com.carrotsearch.hppc.", ()),
    ("RoaringBitmap", "org/roaringbitmap/RoaringBitmap/1.0.0", "org.roaringbitmap.", ()),
    ("lucene-core", "org/apache/lucene/lucene-core/9.5.0", "org.apache.lucene.util.", ()),
    ("lz4-java", "org/lz4/lz4-java/1.8.0", "net.jpountz.", ()),
    ("generics-resolver", "ru/vyarus/generics-resolver/3.0.3", "ru.vyarus.java.generics.resolver.", ()),
]
RESERVES = [
    ("commons-pool2", "org/apache/commons/commons-pool2/2.12.0", "org.apache.commons.pool2.", ()),
    ("icu4j", "com/ibm/icu/icu4j/75.1", "com.ibm.icu.text.", ()),
]
SALT = "live141-holdout-v1"

def locate(coordinate):
    name, version = coordinate.split("/")[-2:]
    p = MAVEN / coordinate / f"{name}-{version}.jar"
    assert p.is_file(), p
    return {"path": str(p), "sha256": sha(p), "coordinate": coordinate}

def rank(entry):
    return hashlib.sha256(f"{SALT}|{entry['tag']}".encode()).hexdigest()

def pick(eligible):
    selected = []
    classes = Counter()
    order = sorted(eligible, key=rank)
    if len({x["class"] for x in eligible}) < 4:
        return None
    for class_cap in (1, 3):
        for floor, ceiling, quota in [(1, 2, 4), (3, 260, 4), (1, 260, 8)]:
            need = quota - sum(floor <= x["bytecode_branches"] <= ceiling for x in selected)
            for x in order:
                if need <= 0 or len(selected) == 8:
                    break
                if floor <= x["bytecode_branches"] <= ceiling and x not in selected and classes[x["class"]] < class_cap:
                    classes[x["class"]] += 1
                    selected.append(x)
                    need -= 1
        if len(selected) == 8:
            return selected
    return None

def main():
    assert not (ROOT / "inputs/manifest.json").exists()
    prior = sorted(set((OVERNIGHT / "protocols").rglob("*.json")) |
                   set((OVERNIGHT / "runs").rglob("independent-manifest.json")) |
                   set((OVERNIGHT / "runs").rglob("methods.jsonl")) |
                   set(CAMPAIGN.glob("dataset-v1/*/manifest.json")) |
                   set(CAMPAIGN.glob("setup/*/manifest.json")) |
                   {ROOT.parent / "interactive/heldout-commitment.json"})
    corpus = {str(p): p.read_text(errors="replace") for p in prior}
    asm = os.pathsep.join(str(p) for p in LOCAL_CP if p.name in ("asm-9.7.1.jar", "asm-tree-9.7.1.jar"))
    scan = BUILD / "scan"
    scan.mkdir(exist_ok=True)
    env = safe_env(scan)
    subprocess.run(["taskset", "-c", "13", str(JDK / "javac"), "-cp", asm, "-d", scan,
                    ROOT / "scripts/ScanMethods.java"], timeout=30, check=True, env=env)
    artifacts = []
    absent = []
    for project, coordinate, prefix, deps in [*SOURCES, *RESERVES]:
        if len(artifacts) == len(SOURCES):
            break
        # Project may appear only if explicitly assigned as a project or in a method tag / Maven JAR.
        project_pattern = re.compile(r'"project"\s*:\s*"' + re.escape(project) + r'"|"' +
                                     re.escape(project) + r'/|/' + re.escape(project) + r'-[0-9]', re.I)
        overlap = [p for p, content in corpus.items() if project_pattern.search(content)]
        assert not overlap, (project, overlap)
        jar = locate(coordinate)
        rows = subprocess.check_output(["taskset", "-c", "13", *jvm(scan, "256m"), "-cp",
                                        str(scan) + os.pathsep + asm, "ScanMethods",
                                        jar["path"], prefix], text=True, env=env, timeout=30)
        (ROOT / "inputs" / f"{project}-eligible.tsv").write_text(rows)
        eligible = []
        for line in rows.splitlines():
            cls, name, desc, size, branches = line.split("\t")
            eligible.append(dict(tag=f"{project}/{cls}.{name}{desc}", **{"class": cls},
                                 bytecode_instructions=int(size), bytecode_branches=int(branches)))
        selected = pick(eligible)
        if selected is None:
            absent.append({"project": project, "eligible_methods": len(eligible),
                           "eligible_classes": len({x["class"] for x in eligible})})
            continue
        for x in selected:
            cls = re.compile(r'(?<![\w.$])' + re.escape(x["class"]) + r'(?![\w$])')
            overlap = [p for p, content in corpus.items() if cls.search(content)]
            assert not overlap, (x["tag"], overlap)
        artifacts.append(dict(project=project, jar=jar, dependencies=[locate(d) for d in deps],
                              package=prefix, eligible=len(eligible), eligible_classes=len({x["class"] for x in eligible}),
                              methods=selected))
    assert len(artifacts) == len(SOURCES), absent
    write(ROOT / "inputs/manifest.json", dict(locked=now(), salt=SALT, projects=artifacts, static_replacements=absent,
                                              prior_files={p: sha(p) for p in corpus}))
    rng = random.Random(1410526)
    jobs = []
    assignments = [0] * len(CORES)
    for p in artifacts:
        orders = list(itertools.permutations(ARMS))
        rng.shuffle(orders)
        core_order = rng.sample(CORES, len(CORES))
        used = dict.fromkeys(CORES, 0)
        for rep, arms in enumerate(orders, start=1):
            core = min(core_order, key=lambda core: (used[core], assignments[CORES.index(core)]))
            assignments[CORES.index(core)] += 1
            used[core] += 1
            tags = [m["tag"] for m in p["methods"]]
            rng.shuffle(tags)
            jobs.append(dict(id=f"{p['project']}-r{rep}", project=p["project"], rep=rep,
                             core=core, arms=list(arms), tags=tags))
    rng.shuffle(jobs)
    for ordinal, job in enumerate(jobs, start=1):
        job["ordinal"] = ordinal
    write(ROOT / "inputs/schedule.json", dict(locked=now(), seed=1410526, jobs=jobs))
    for p in ("manifest", "schedule"):
        print(p, sha(ROOT / "inputs" / f"{p}.json"))

if __name__ == "__main__":
    main()
