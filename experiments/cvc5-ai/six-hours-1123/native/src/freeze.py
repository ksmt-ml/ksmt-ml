"""Generate algebraic SAT/UNSAT pairs and freeze the exact native workloads."""
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base

OPTIONS={
    "default":{},
    "array-index":{"arrays-eager-index":"false"},
    "bv-eager":{"bv-eager-eval":"true"},
    "no-simplification":{"simplification":"none"},
    "no-static-learning":{"static-learning":"false"},
    "arrays-exp":{"arrays-exp":"true"},
    "bv-to-bool":{"bv-to-bool":"true"},
}
FAMILIES=("ring","partial-products","swap","read-after-write")

def bv(n,width):
    return f"(_ bv{n % (1<<width)} {width})"

def add(a,b):
    return f"(bvadd {a} {b})"

def mul(a,b):
    return f"(bvmul {a} {b})"

def select(a,i):
    return f"(select {a} {i})"

def store(a,i,v):
    return f"(store {a} {i} {v})"

def get_formula(family,width,i,k):
    const=bv(k,width)
    z=bv(0,width)
    guard=f"(= ((_ extract 0 0) x) #b{i%2})"
    if family=="ring":
        a=add("x",const)
        a=f"((_ rotate_left {i+1}) {a})"
        if i%2:
            left=mul(a,add("y","z"))
            right=add(mul(a,"y"),mul(a,"z"))
        else:
            left=mul(add(a,"y"),"z")
            right=add(mul(a,"z"),mul("y","z"))
    elif family=="partial-products":
        fl=f"((_ rotate_left {i+1}) x)"
        r=f"(bvxor y {const})"
        left=mul(fl,r)
        right=z
        for bit in range(width):
            part=f"(ite (= ((_ extract {bit} {bit}) {r}) #b1) (bvshl {fl} {bv(bit,width)}) {z})"
            right=add(right,part)
    elif family=="swap":
        x=f"(bvxor x {const})"
        y=f"((_ rotate_right {i+1}) y)"
        v=add("z",const)
        guard=f"(distinct {x} {y})"
        left=store(store("a",x,"z"),y,v)
        right=store(store("a",y,v),x,"z")
    else:
        x=f"((_ rotate_left {i+1}) x)"
        y=f"(bvxor y {const})"
        v=add("z",const)
        left=select(store(store("a",y,"z"),x,v),"q")
        right=f"(ite (= q {x}) {v} (ite (= q {y}) z (select a q)))"
    return guard,f"(= {left} {right})"

def program(family,width,constants,flip):
    array=family in ("swap","read-after-write")
    typ=f"(_ BitVec {width})"
    lines=["(set-logic "+("QF_ABV" if array else "QF_BV")+")"]
    for x in ("x","y","z","q"):
        lines.append(f"(declare-fun {x} () {typ})")
    if array:
        lines.append(f"(declare-fun a () (Array {typ} {typ}))")
    oracle=[]
    for i,k in enumerate(constants):
        guard,predicate=get_formula(family,width,i,k)
        values=(True,False) if (i+flip)%2==0 else (False,True)
        for expected_sat in values:
            check=predicate if expected_sat else f"(not {predicate})"
            lines.extend(["(push 1)",f"(assert {guard})",f"(assert {check})","(check-sat)","(pop 1)"])
            oracle.append("sat" if expected_sat else "unsat")
    return "\n".join(lines)+"\n",oracle

def main():
    out=ROOT/"inputs/manifest.json"
    if out.exists():
        return
    dest=ROOT/"inputs/streams"
    dest.mkdir(exist_ok=True)
    rng=random.Random(23030141)
    programs=[]
    for f in FAMILIES:
        for n in range(8):
            w=(7+n//2) if f=="partial-products" else (7+n//2) if f=="ring" else (8+4*(n//2))
            c=rng.sample(range(1,1<<w),k=4)
            text,expected=program(f,w,c,n%2)
            p=dest/f"{f}-{n+1:02d}.smt2"
            p.write_text(text)
            programs.append(dict(id=p.stem,family=f,width=w,constants=c,path=str(p.relative_to(ROOT)),
                   size=p.stat().st_size,sha256=base.sha(p),expected=expected))
    jobs=[]
    for i,p in enumerate(programs):
        arms=list(OPTIONS)
        rng.shuffle(arms)
        shift=rng.sample(range(len(arms)),k=3)
        for rep,r in enumerate(shift):
            jobs.append(dict(id=p["id"]+f"-r{rep+1}",program=p["id"],rep=rep+1,
                             core=base.CPUS[(i+rep)%4],order=arms[r:]+arms[:r]))
    rng.shuffle(jobs)
    base.write(out,dict(created=base.now(),sha_protocol=base.sha(ROOT.parent/"protocols/03-native-theories.md"),
                  option_sets=OPTIONS,programs=programs,jobs=jobs,experiment_seed=23030141))
    print("frozen streams",len(programs),"paired jobs",len(jobs),flush=True)

if __name__=="__main__":
    main()
