"""Create disjoint formula families before any search measurements."""
from pathlib import Path
import os
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base

FAMILIES={
    "train":("segmented-product","remainder-idempotence"),
    "validation":("signed-multiply","packed-sum"),
    "test":("weighted-product","unsigned-remainder","difference-square-cross","rescaled-add"),
}
# Small, ordinary numerical operations with independent algebraic SAT or UNSAT answers.
WIDTH={"segmented-product":8,"remainder-idempotence":8,"signed-multiply":8,"packed-sum":21,
       "weighted-product":9,"unsigned-remainder":8,"difference-square-cross":9,"rescaled-add":24}

def B(op,x,y):
    return f"({op} {x} {y})"

def C(value,w):
    return f"(_ bv{value%(1<<w)} {w})"

def get(stream,w,r,idx):
    x=f"((_ rotate_left {idx+1}) x)"
    y=f"(bvxor y {C(r,w)})"
    zero=C(0,w)
    guard=f"(= ((_ extract 0 0) {x}) #b{idx%2})"
    if stream=="segmented-product":
        m=w//2
        a=f"((_ zero_extend {w-m}) ((_ extract {m-1} 0) {x}))"
        b=f"((_ zero_extend {m}) ((_ extract {w-1} {m}) {x}))"
        left=B("bvmul",x,y)
        right=B("bvadd",B("bvmul",a,y),B("bvshl",B("bvmul",b,y),C(m,w)))
    elif stream=="remainder-idempotence":
        guard=f"(and {guard} (distinct {y} {zero}))"
        mod=B("bvurem",x,y)
        left=B("bvurem",mod,y)
        right=mod
    elif stream=="signed-multiply":
        guard=f"(and {guard} (bvsge {x} {zero}) (bvsge {y} {zero}))"
        left=B("bvmul",x,f"(bvneg {y})")
        right=f"(bvneg {B('bvmul',x,y)})"
    elif stream=="packed-sum":
        shift=C(w//2,w)
        left=B("bvshl",B("bvadd",x,y),shift)
        right=B("bvadd",B("bvshl",x,shift),B("bvshl",y,shift))
    elif stream=="weighted-product":
        z=f"((_ rotate_right {idx+2}) z)"
        left=B("bvmul",B("bvadd",x,y),B("bvsub",z,y))
        right=B("bvsub",B("bvadd",B("bvmul",x,z),B("bvmul",y,z)),B("bvadd",B("bvmul",x,y),B("bvmul",y,y)))
    elif stream=="unsigned-remainder":
        guard=f"(and {guard} (distinct {y} {zero}))"
        left=B("bvult",B("bvurem",x,y),y)
        right="true"
    elif stream=="difference-square-cross":
        sumxy=B("bvadd",x,y)
        left=B("bvsub",B("bvmul",sumxy,sumxy),B("bvadd",B("bvmul",x,x),B("bvmul",y,y)))
        right=B("bvshl",B("bvmul",x,y),C(1,w))
    elif stream=="rescaled-add":
        left=B("bvadd",x,y)
        right=B("bvadd",B("bvxor",x,y),B("bvshl",B("bvand",x,y),C(1,w)))
    else:
        raise ValueError(stream)
    return guard,f"(= {left} {right})"

def freeze():
    dest=ROOT/"inputs/streams.json"
    if dest.exists():
        return base.read(dest)
    rng=random.Random(23090141)
    out=ROOT/"inputs/streams"
    out.mkdir(exist_ok=True)
    data={}
    for part,families in FAMILIES.items():
        data[part]=[]
        for code in families:
            count={"train":3,"validation":4,"test":6}[part]
            for i in range(count):
                w=WIDTH[code]+(i//2)%3
                file=out/f"{code}-{i+1:02d}.smt2"
                lines=["(set-logic QF_BV)",*[f"(declare-fun {v} () (_ BitVec {w}))" for v in ("x","y","z")]]
                expected=[]
                for j in range(3):
                    g,prop=get(code,w,rng.randrange(1,1<<w),j)
                    results=[("sat",prop),("unsat",f"(not {prop})")]
                    if (i+j)%2:
                        results.reverse()
                    for status,formula in results:
                        lines.extend(["(push 1)",f"(assert {g})",f"(assert {formula})","(check-sat)","(pop 1)"])
                        expected.append(status)
                file.write_text("\n".join(lines)+"\n")
                data[part].append(dict(id=file.stem,family=code,width=w,expected=expected,
                    path=str(file.relative_to(ROOT)),sha256=base.sha(file)))
    protocol=ROOT.parent/"protocols/09-native-search.md"
    result=dict(frozen=base.now(),splits=data,protocol=base.sha(protocol),seed=23090141)
    base.write(dest,result)
    return result

if __name__=="__main__":
    data=freeze()
    print({k:len(v) for k,v in data["splits"].items()},flush=True)
