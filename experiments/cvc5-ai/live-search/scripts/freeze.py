import random
from base import *

TRAIN = ("automaton","re2j","RoaringBitmap","commons-pool2")
VALID = ("lz4-java","generics-resolver","packageurl-java")
TEST = ("hppc","lucene-core","jsoup","commons-validator")
SALT = "live-search-141-2026-11-22"
SCHEDULE_SEED = 141626

def rank(s):
    return archive.hashlib.sha256((SALT+"\n"+s).encode()).hexdigest()

def outer(cls):
    return cls.split("$",1)[0]

def plans(projects, section, repeats, rng):
    jobs=[]
    for index,p in enumerate(projects):
        rotations=list(range(4))
        rng.shuffle(rotations)
        labels=["default","smac","sol","z3"]
        rng.shuffle(labels)
        for rep in range(repeats):
            tags=[x["tag"] for x in p["methods"]]
            rng.shuffle(tags)
            n=rotations[rep]
            jobs.append(dict(id=f"{p['project']}-r{rep+1}",project=p["project"],rep=rep+1,seed=(0,71)[rep%2],
                core=CPUS[(index+rep)%4],tags=tags,order=labels[n:]+labels[:n] if section=="test" else []))
    rng.shuffle(jobs)
    return jobs

def main():
    historic=read(OLD/"inputs/manifest.json")
    previous={name:sha(name) for name in historic["prior_files"] if Path(name).is_file()}
    all_prior=tuple(Path(name).read_text(errors="replace") for name in previous)
    old={p["project"]:p for p in historic["projects"]}
    packagejar=ROOT.parents[2]/".m2/repository/com/github/package-url/packageurl-java/1.4.1/packageurl-java-1.4.1.jar"
    assert packagejar.is_file(), packagejar
    output={"locked":now(),"salt":SALT,"prior_manifest":sha(OLD/"inputs/manifest.json"),
            "earlier_files_rechecked":previous,"splits":{},"eligibility":{}}
    for section,names in (("train",TRAIN),("validation",VALID),("test",TEST)):
        projects=[]
        for name in names:
            p=dict(old[name]) if name in old else dict(project=name,package="com.github.packageurl.",
                jar={"path":str(packagejar),"sha256":sha(packagejar)},dependencies=[])
            if section=="test" or name=="packageurl-java":
                prev={outer(m["class"]) for m in p.get("methods",[])}
                records=[]
                src=OLD/f"inputs/{name}-eligible.tsv"
                for line in src.read_text().splitlines():
                    cls,meth,signature,ops,branches=line.split("\t")
                    tag=f"{name}/{cls}.{meth}{signature}"
                    if outer(cls) in prev or any(outer(cls) in body or outer(cls).replace(".","/") in body
                                                for body in all_prior):
                        continue
                    records.append(dict(tag=tag,**{"class":cls},bytecode_instructions=int(ops),bytecode_branches=int(branches)))
                if section=="test":
                    chosen=[]
                    used=set()
                    for high in (False, True):
                        picked=[]
                        for m in sorted(records,key=lambda m:rank(m["tag"])):
                            if (m["bytecode_branches"]>=3)==high and outer(m["class"]) not in used:
                                picked.append(m)
                                used.add(outer(m["class"]))
                                if len(picked)==4:
                                    break
                        assert len(picked)==4, (name,high,len(picked))
                        chosen.extend(picked)
                else:
                    chosen=records
                    assert len(chosen)==4
                p["methods"]=chosen
                output["eligibility"][name]=dict(previous_outer_classes=sorted(prev),remaining=len(records),source_sha256=sha(src))
            for asset in (p["jar"],*p["dependencies"]):
                assert sha(asset["path"])==asset["sha256"]
            projects.append(p)
        output["splits"][section]=projects
    rng=random.Random(SCHEDULE_SEED)
    arm_order=["smac","sol"]
    rng.shuffle(arm_order)
    schedule=dict(seed=SCHEDULE_SEED,arm_order=arm_order,
                  **{n:plans(p,n,4 if n=="test" else 2,rng) for n,p in output["splits"].items()})
    dest=ROOT/"inputs"
    for f,data in [(dest/"manifest.json",output),(dest/"schedule.json",schedule)]:
        assert not f.exists()
        write(f,data)
    write(dest/"seal.json",dict(locked=now(),**{f.name:sha(f) for f in
        (ROOT/"PREREG.md",dest/"manifest.json",dest/"schedule.json",ROOT/"evidence/historical.json")}))
    for n,p in output["splits"].items():
        print(n,[(x["project"],len(x["methods"])) for x in p])
    print(arm_order,output["locked"])

if __name__=="__main__":
    main()
