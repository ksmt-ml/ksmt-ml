"""Enumerate the clustered bootstrap exactly as a post-hoc numerical audit."""
from itertools import product
import os

from common import ROOT, now, read, sha, write

source=ROOT/"evidence/analysis.json"
data=read(source)
snapshot=ROOT/"evidence/exact-bootstrap-source.json"
tmp=snapshot.with_suffix(".tmp")
tmp.write_bytes(source.read_bytes())
os.replace(tmp,snapshot)
result={}
for producer,d in data["test"].items():
    scopes=("sequential","combinational") if producer=="hw-search" else ("overall",)
    for scope in scopes:
        comparisons=d[scope]["contrasts"]
        output={}
        for name,stat in comparisons.items():
            points=list(stat["families"].values())
            n=len(points)
            assert stat["n"]==n and 0<n<=6
            a,b=zip(*points)
            ratios=[]
            for sample in product(range(n),repeat=n):
                ratios.append(sum(a[i] for i in sample)/sum(b[i] for i in sample))
            ratios.sort()
            interval=[ratios[int(q*(len(ratios)-1))] for q in (.025,.975)]
            delta=[x-y for x,y in points]
            observed=abs(sum(delta))
            all_flips=2**n
            outward=sum(abs(sum(s*d for s,d in zip(sign,delta)))+1e-12>=observed
                        for sign in product((-1,1),repeat=n))
            output[name]=dict(n=n,theta=stat["relative"],exact_ratio_ci=interval,
                              preregistered_mc_ci=stat["ratio_ci"],exact_bootstrap_samples=len(ratios),
                              exact_sign_flip_fraction=outward/all_flips,leave_one_out=stat["leave_one_out_ratio"])
        result[f"{producer}/{scope}"]=output
dest=ROOT/"evidence/exact-bootstrap-audit.json"
write(dest,dict(at=now(),analysis_sha256=sha(snapshot),source=str(snapshot.relative_to(ROOT)),
                method="enumeration of independent-family resamples and sign flips; diagnostic, no test selection",
                panels=result))
print(now(),{k:len(v) for k,v in result.items()},flush=True)
