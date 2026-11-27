"""An independent arithmetic replication of two effective settings."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base

OPTIONS={
  "default":{},"default-repeat":{},"gauss":{"bv-gauss-elim":"true"},
  "bitwise":{"bitwise-eq":"true"},
  "joint":{"bv-gauss-elim":"true","bitwise-eq":"true"},
}
FAMILIES={
    "bounded-product":[8,9,10],
    "divide-remainder":[6,7,8],
    "difference-squares":[6,7,8],
    "split-carry":[16,20,24],
}

def main():
    dest=ROOT/"inputs/manifest.json"
    if dest.exists():
        return
    source=ROOT.parent/"bv-study/src/freeze.py"
    spec=spec_from_file_location("bv_programs",source)
    module=module_from_spec(spec)
    spec.loader.exec_module(module)
    root=ROOT/"inputs/streams"
    root.mkdir(exist_ok=True)
    rng=random.Random(23110902)
    programs=[]
    for f,widths in FAMILIES.items():
        for idx in range(12):
            w=widths[idx%len(widths)]
            words,oracle,gen=module.program(f,w,idx,rng)
            path=root/f"{f}-new{idx+1:02d}.smt2"
            path.write_text(words)
            programs.append(dict(id=path.stem,family=f,width=w,expected=oracle,constants=gen,
                                    path=str(path.relative_to(ROOT)),sha256=base.sha(path)))
    jobs=[]
    for i,p in enumerate(programs):
        best=list(OPTIONS)
        rng.shuffle(best)
        order=rng.sample(range(len(best)),3)
        for rep,k in enumerate(order):
            jobs.append(dict(id=p["id"]+f"-r{rep+1}",program=p["id"],core=base.CPUS[(i+rep)%4],
                          rep=rep+1,order=best[k:]+best[:k]))
    rng.shuffle(jobs)
    base.write(dest,dict(at=base.now(),sha_protocol=base.sha(ROOT.parent/"protocols/09b-interactions.md"),
                            generator=base.sha(source),programs=programs,option_sets=OPTIONS,jobs=jobs,
                            experiment_seed=23110902))
    print("replication",len(programs),"programs and",len(jobs),"blocks",flush=True)

if __name__=="__main__":
    main()
