"""Generate fresh verification conditions through the original Why3 driver."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re

from common import ROOT, CPUS, now, read, run, sha, write
from eval import blocked, summarize

SRC=ROOT/"inputs/why3-source"
DATA=ROOT/"inputs/why3-generated"
DRIVER="/usr/share/why3/drivers/cvc5.drv"
LOCK=ROOT/"evidence/opt-search-lock.json"
TRAILER='(error "Can\'t get-info :reason-unknown when the last result wasn\'t unknown!")'


def proof_view(rows):
    """Preserve full raw runs and audit the first proof answer separately."""
    normalized=[]
    for row in rows:
        location=ROOT/row["location"]
        process=read(location/"process.json")
        stdout=(location/"stdout").read_text().splitlines()
        stderr=(location/"stderr").read_text().strip()
        accepted=(row["status"]=="error" and not process["timeout"] and process["ret"]==0 and not stderr
                  and row["expected"] in ("sat","unsat") and row["answers"]==[row["expected"]]
                  and [s for s in stdout if s.strip()]==[row["expected"],TRAILER])
        if accepted:
            normalized.append(dict(row,status="solved",par2=row["seconds"],verified_checks=1,
                                   raw_status=row["status"],trailing_driver_diagnostic=True))
        else:
            normalized.append(dict(row,raw_status=row["status"],trailing_driver_diagnostic=False))
    return normalized


def build():
    meta=DATA/"manifest.json"
    if meta.exists():
        return read(meta)
    assert LOCK.is_file()
    items=[]
    rejected=[]
    for path in sorted(SRC.glob("*.mlw")):
        rel=path.relative_to(ROOT)
        where=ROOT/"raw/why3-generate"/path.stem
        cmd=[ROOT/"scripts/why3-wrap.sh",where,"prove","-D",DRIVER,"-o","/work",
             "/campaign/"+str(rel)]
        r=run(where,cmd,CPUS[0],7.)
        outputs=sorted(where.glob("*.smt2"))
        if r["ret"]!=0 or not outputs:
            rejected.append(dict(source=str(rel),ret=r["ret"],start=r["start"],stop=r["stop"]))
        for i,p in enumerate(outputs):
            text=p.read_text()
            number=len(re.findall(r"(?m)^\s*\(check-sat\s*\)",text))
            if number!=1:
                rejected.append(dict(source=str(rel),output=str(p.relative_to(ROOT)),checks=number))
                continue
            ident=f"why3-{path.stem.lower()}-g{i+1}"
            out=DATA/(ident+".smt2")
            out.parent.mkdir(parents=True,exist_ok=True)
            out.write_bytes(p.read_bytes())
            expected="sat" if path.stem=="Wrong" else "unsat"
            items.append(dict(id=ident,family=path.stem,producer="why3",path=str(out.relative_to(ROOT)),
                              sha256=sha(out),expected=expected,source=str(rel),source_hash=sha(path)))
    result=dict(at=now(),items=items,rejected=rejected,lock=sha(LOCK),driver=DRIVER)
    write(meta,result)
    return result


def measure():
    built=build()
    items=built["items"]
    dst=ROOT/"raw/why3-transfer"
    oracle=blocked(dst/"z3",items,{"Z3":[]},18.,1,1398101,"z3")
    oracle_proof=proof_view(oracle)
    reject={r["id"] for r in oracle_proof if r["status"] in ("wrong","error","incomplete")}
    items=[row for row in items if row["id"] not in reject]
    lock=read(LOCK)
    arms={"0":[],"AA":[],"fixed":lock["contrast"]}
    seen={tuple(flags) for flags in arms.values()}
    for name,row in lock["selected"].items():
        opts=row["flags"]
        if tuple(opts) not in seen:
            seen.add(tuple(opts))
            arms[name]=opts
    rows=blocked(dst/"cvc5",items,arms,7.,2,1398102)
    proofs=proof_view(rows)
    write(dst/"proof-view.json",proofs)
    report=dict(at=now(),oracles=summarize(oracle_proof),excluded=sorted(reject),
                generated=len(built["items"]),translation_failures=built["rejected"],results=summarize(proofs),
                full_transcript_results=summarize(rows),trailing_driver_diagnostics=sum(r["trailing_driver_diagnostic"] for r in proofs))
    write(dst/"summary.json",report)
    print(report,flush=True)


if __name__=="__main__":
    measure()
