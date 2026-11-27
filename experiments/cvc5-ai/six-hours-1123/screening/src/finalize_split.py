"""Freeze a broader static heldout set with one class allowed per project."""
from pathlib import Path
import sys

from freeze import ROOT,base,COORDINATES,locate,select,read_history
from extend import RESERVES

def main():
    out=ROOT/"inputs/research-split.json"
    if out.exists():
        return
    prior=ROOT/"inputs/expanded-split.json"
    structure=base.read(prior)
    history,hashes=read_history()
    assert structure["history"]==hashes
    used={p["project"] for ps in structure["splits"].values() for p in ps}
    for project,(split,coord,_,extra) in (COORDINATES|RESERVES).items():
        if split!="test" or project in used:
            continue
        paths=[ROOT/"evidence"/stage/f"{project}.tsv" for stage in ("scan","scan-reserve")]
        source=next((p for p in paths if p.is_file()),None)
        if not source:
            continue
        picked,seen=select(project,source,history)
        assets=[locate(p) for p in (coord,*extra)]
        if not picked or not all(assets):
            continue
        structure["splits"]["test"].append(dict(project=project,files=assets,methods=picked,
                         eligible=seen,scan_sha=base.sha(source),singleton=len(picked)==1))
        structure["excluded"].pop(project,None)
    structure["prior_sha256"]=base.sha(prior)
    structure["heldout_frozen_at"]=base.now()
    structure["heldout_selection_changes"]="Singleton projects included for independent clustering. Static evidence only."
    assert sum(len(v) for v in structure["splits"].values())==len(
        {p["project"] for v in structure["splits"].values() for p in v})
    base.write(out,structure)
    print({k:{p["project"]:len(p["methods"]) for p in v} for k,v in structure["splits"].items()})

if __name__=="__main__":
    main()
