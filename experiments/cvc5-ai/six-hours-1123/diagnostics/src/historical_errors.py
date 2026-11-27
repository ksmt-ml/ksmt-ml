"""Attribute raw step errors using both their message and first relevant frame."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"recovery/scripts"))
import base

CATEGORIES = (
    ("ksmt_cvc5_fp_ieee", lambda s: "KSolverUnsupportedFeatureException" in s and "fp.to_ieee_bv" in s),
    ("usvm_array_reflection_cast", lambda s: "ClassCastException" in s and "JcApproximations.kt:394" in s),
    ("usvm_null_field", lambda s: "Got nullRef on the top!" in s and "FieldsRegion.kt:96" in s),
    ("usvm_stepscope", lambda s: "StepScope.doWithState" in s or "StepScope.assert" in s),
    ("jacodb_missing_class", lambda s: "Could not find type" in s and "JcInstListBuilder" in s),
)

def groups(path):
    active=None
    error=None
    outcome=[]
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("STOCK_METHOD_START "):
            active=line.partition(" ")[2]
        elif line.startswith("STOCK_METHOD_END "):
            if error is not None:
                outcome.append((active,error))
                error=None
            active=None
        elif active and re.search(r"\bStep failed\s*$",line):
            if error is not None:
                outcome.append((active,error))
            error=[]
        elif active and error is not None and len(error)<60:
            error.append(line)
    if active and error is not None:
        outcome.append((active,error))
    return outcome

def main():
    result={}
    for section in ("selection","pre-restart","post-restart"):
        folder=ROOT/"recovery/raw"/section
        total,counts=Counter(),defaultdict(Counter)
        examples={}
        for f in sorted(folder.rglob("stdout.log")):
            if "model" in f.parts:
                continue
            invocation=base.read(f.parent/"invocation.json")
            for tag,lines in groups(f):
                snippet="\n".join(lines)
                category=next((k for k,p in CATEGORIES if p(snippet)),"other")
                total[category]+=1
                counts[invocation["arm"]][category]+=1
                examples.setdefault(category,dict(tag=tag,path=str(f.relative_to(ROOT)),message=lines[:8]))
        result[section]=dict(counts=total,by_jvm_label=counts,examples=examples)
    base.write(ROOT/"diagnostics/evidence/historical-errors.json",result)
    for k,v in result.items():
        print(k,dict(v["counts"]))

if __name__=="__main__":
    main()
