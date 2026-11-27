"""Freeze algebraic identities across widths with a mathematical oracle."""
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"recovery/scripts"))
import base

PROTOCOL=TOP/"protocols/19-bv-width.md"
OPTIONS = {"default":{}, "default-repeat":{}, "gauss":{"bv-gauss-elim":"true"},
           "short":{"tlimit-per":"400"},"short-gauss":{"tlimit-per":"400","bv-gauss-elim":"true"}}
WIDTHS=(7,9,11,13,14)


def make(family,w,seed):
    typed=f"(_ BitVec {w})"
    x=f"((_ rotate_left {seed+1}) x)"
    y=f"(bvxor y (_ bv{seed*3+1} {w}))"
    xx=f"(bvmul {x} {x})"
    yy=f"(bvmul {y} {y})"
    xy=f"(bvmul {x} {y})"
    plus=f"(bvadd {x} {y})"
    minus=f"(bvsub {x} {y})"
    plus2=f"(bvmul {plus} {plus})"
    minus2=f"(bvmul {minus} {minus})"
    lhs,rhs={
        "difference": (f"(bvmul {plus} {minus})",f"(bvsub {xx} {yy})"),
        "expand-plus": (f"(bvsub {plus2} (bvadd {xx} {yy}))",f"(bvadd {xy} {xy})"),
        "expand-minus": (minus2,f"(bvsub (bvadd {xx} {yy}) (bvadd {xy} {xy}))"),
        "gap": (f"(bvsub {plus2} {minus2})",f"(bvshl {xy} (_ bv2 {w}))"),
    }[family]
    base_guard=f"(= ((_ extract 0 0) {x}) #b{seed%2})"
    known=f"(assert (distinct {lhs} {rhs}))"
    text=["(set-logic QF_BV)",f"(declare-fun x () {typed})",f"(declare-fun y () {typed})",
          f"(assert {base_guard})","(check-sat)","(push 1)",known,"(check-sat)","(pop 1)",
          "(push 1)",f"(assert (= ((_ extract 1 1) {y}) #b1))","(check-sat)",known,"(check-sat)",
          "(pop 1)",f"(assert (= {lhs} {rhs}))","(check-sat)"]
    return "\n".join(text)+"\n", ["sat","unsat","sat","unsat","sat"]


def freeze():
    dest=ROOT/"inputs/manifest.json"
    if dest.exists():
        return base.read(dest)
    rng=random.Random(23111901)
    programs=[]
    jobs=[]
    for family in ("difference","expand-plus","expand-minus","gap"):
        for seed in (0,1):
            for width in WIDTHS:
                tag=f"{family}-w{width:02d}-s{seed}"
                where=ROOT/"inputs/streams"/(tag+".smt2")
                body,answers=make(family,width,seed)
                where.write_text(body)
                programs.append(dict(id=tag,family=family,width=width,variant=seed,expected=answers,
                    path=str(where.relative_to(ROOT)),sha256=base.sha(where)))
                order=list(OPTIONS)
                rng.shuffle(order)
                for rep,shift in enumerate(rng.sample(range(len(order)),2)):
                    jobs.append(dict(id=tag+f"-r{rep+1}",program=tag,core=base.CPUS[(rep+len(programs))%4],
                        order=order[shift:]+order[:shift],rep=rep+1))
    rng.shuffle(jobs)
    d=dict(at=base.now(),sha_protocol=base.sha(PROTOCOL),programs=programs,option_sets=OPTIONS,jobs=jobs)
    base.write(dest,d)
    return d


if __name__=="__main__":
    print(len(freeze()["jobs"]))
