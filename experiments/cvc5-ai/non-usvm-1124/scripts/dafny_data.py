"""Manifest whole fresh Dafny programs with independently specified outcomes."""
from common import ROOT, now, sha, write

CASES={
    "Quotient":("division-loop",[1,0]),
    "Square":("odd-sum-loop",[1,0]),
    "Maximum":("prefix-maximum",[2,0]),
    "Concat":("slice-identity",[1,0]),
    "Permutation":("sequence-permutation",[2,0]),
    "Interval":("negative-control",[1,1]),
}
folder=ROOT/"inputs/base/train/dafny"
rows=[]
for name,(family,verdict) in CASES.items():
    path=folder/f"{name}.dfy"
    rows.append(dict(id=name,family=family,producer="dafny",expected=verdict,
                     path=str(path.relative_to(ROOT)),sha256=sha(path)))
write(folder/"manifest.json",dict(at=now(),items=rows))
