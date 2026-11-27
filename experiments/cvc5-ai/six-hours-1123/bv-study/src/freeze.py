"""Known arithmetic workloads for the backend option ablation."""
from hashlib import sha256
from math import isqrt
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base

OPTIONS={
    "default":{},
    "eager-bitblast":{"bitblast":"eager"},
    "internal-bitblast":{"bv-solver":"bitblast-internal"},
    "assert-input":{"bv-assert-input":"true"},
    "no-propagate":{"bv-propagate":"false"},
    "no-equality-engine":{"bv-eq-engine":"false"},
    "gauss":{"bv-gauss-elim":"true"},
    "abstract":{"bv-abstraction":"true","bv-abstraction-size":"8"},
    "bitwise":{"bitwise-eq":"true"},
}
FAMILIES=("bounded-product","divide-remainder","difference-squares","split-carry")

def bv(value,w):
    return f"(_ bv{value % (1 << w)} {w})"

def ext(value,w):
    return f"((_ zero_extend {w}) {value})"

def mul(a,b):
    return f"(bvmul {a} {b})"

def add(a,b):
    return f"(bvadd {a} {b})"

def sub(a,b):
    return f"(bvsub {a} {b})"

def prime(n):
    return n>=2 and all(n%i for i in range(2,isqrt(n)+1))

def choose_prime(rng,lo,hi):
    for _ in range(300):
        n=rng.randrange(lo,hi)|1
        if prime(n):
            return n
    raise RuntimeError((lo,hi))

def formulas(family,w,i,rng):
    mix=rng.randrange(1,1<<w)
    word=f"((_ rotate_left {i+1}) x)"
    z=bv(0,w)
    guard=f"(= ((_ extract 0 0) x) #b{i%2})"
    if family=="bounded-product":
        bounds=f"(and (bvuge x {bv(2,w)}) (bvuge y {bv(2,w)}) (bvule x y))"
        lo=1<<(w-2)
        px=rng.randrange(lo,1<<(w-1))
        py=rng.randrange(lo,1<<(w-1))
        target=px*py
        other=choose_prime(rng,target,target+400)
        prod=mul(ext("x",w),ext("y",w))
        return bounds, f"(= {prod} {bv(target,2*w)})", f"(= {prod} {bv(other,2*w)})", [px,py,target,other]
    if family=="divide-remainder":
        d=f"(bvxor y {bv(mix,w)})"
        guard=f"(and {guard} (distinct {d} {z}))"
        quotient=f"(bvudiv {word} {d})"
        remain=f"(bvurem {word} {d})"
        equal=f"(= {word} {add(mul(quotient,d),remain)})"
    elif family=="difference-squares":
        d=f"(bvxor y {bv(mix,w)})"
        equal=f"(= {mul(add(word,d),sub(word,d))} {sub(mul(word,word),mul(d,d))})"
    else:
        low=w//2
        high=w-low
        a=f"((_ extract {low-1} 0) {word})"
        b=f"((_ extract {low-1} 0) y)"
        c=f"((_ extract {w-1} {low}) {word})"
        d=f"((_ extract {w-1} {low}) y)"
        low_sum=add(ext(a,1),ext(b,1))
        carry=f"((_ zero_extend {high-1}) ((_ extract {low} {low}) {low_sum}))"
        combined=f"(concat {add(add(c,d),carry)} ((_ extract {low-1} 0) {low_sum}))"
        equal=f"(= {add(word,'y')} {combined})"
    return guard,equal,f"(not {equal})",[mix]

def program(f,w,n,rng):
    head=f"(set-logic QF_BV)\n(declare-fun x () (_ BitVec {w}))\n(declare-fun y () (_ BitVec {w}))\n"
    rows=[]
    expected=[]
    placements=[]
    for i in range(4):
        guard,sat,unsat,static=formulas(f,w,i,rng)
        placements.append(static)
        arms=[("sat",sat),("unsat",unsat)]
        if (n+i)%2:
            arms.reverse()
        for status,predicate in arms:
            rows.extend(["(push 1)",f"(assert {guard})",f"(assert {predicate})","(check-sat)","(pop 1)"])
            expected.append(status)
    return head+"\n".join(rows)+"\n",expected,placements

def main():
    dest=ROOT/"inputs/manifest.json"
    if dest.exists():
        return
    seeds=23050141
    rng=random.Random(seeds)
    root=ROOT/"inputs/streams"
    root.mkdir(exist_ok=True)
    programs=[]
    for f in FAMILIES:
        for n in range(8):
            width=(8 if f in ("bounded-product","divide-remainder") else 11 if f=="difference-squares" else 24)+n//2
            file=root/f"{f}-{n+1:02d}.smt2"
            text,expected,constants=program(f,width,n,rng)
            file.write_text(text)
            programs.append(dict(id=file.stem,family=f,width=width,path=str(file.relative_to(ROOT)),
                   expected=expected,constants=constants,sha256=base.sha(file)))
    jobs=[]
    for i,p in enumerate(programs):
        order=list(OPTIONS)
        rng.shuffle(order)
        shifts=rng.sample(range(len(order)),3)
        for r,s in enumerate(shifts):
            jobs.append(dict(id=f"{p['id']}-r{r+1}",program=p["id"],rep=r+1,core=base.CPUS[(i+r)%4],
                             order=order[s:]+order[:s]))
    rng.shuffle(jobs)
    base.write(dest,dict(created=base.now(),sha_protocol=base.sha(ROOT.parent/"protocols/05-bv-backend.md"),
                  option_sets=OPTIONS,programs=programs,jobs=jobs,experiment_seed=seeds))
    print("BV sealed:",len(programs),"programs,",len(jobs),"matched blocks",flush=True)

if __name__=="__main__":
    main()
