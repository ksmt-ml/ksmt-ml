"""Check live dump fidelity, exact cvc5 command replay and SAT witnesses."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import re

from common import ROOT, BMC, CVC, Z3, CPUS, environment, now, read, run, sha, write
from eval import ANSWER, manifest
from hw_witness import check

TOKEN=re.compile(r';[^\n]*|"(?:[^"]|"")*"|\|[^|]*\||[()]|[^\s();]+')
ANNOTATION=["(","set-info",":status"]


def commands(path,strip_annotations):
    depth=0
    forms=[]
    current=[]
    for tok in TOKEN.findall(path.read_text()):
        if tok.startswith(";"):
            continue
        if tok=="(":
            depth+=1
        if tok==")":
            depth-=1
        current.append(tok)
        if depth==0:
            if not (strip_annotations and current[:3]==ANNOTATION):
                forms.append(current)
            current=[]
    if depth:
        raise ValueError(f"incomplete form in {path}")
    return forms


def audit(row,cpu,cap,outdir,opts):
    folder=outdir/row["id"]
    out=folder/"summary.json"
    if out.is_file():
        return read(out)
    wire=folder/"capture"
    cmd=[BMC,"-s","cvc5","--noprogress","--presat","--dump-smt2",wire/"dump.smt2",
         "--dummy",wire/"frontend-replies.txt","-t",str(row["depth"])]
    if row["expected"]=="sat":
        cmd+=["--dump-vcd",wire/"sat.vcd"]
    for o in opts:
        cmd+=["-S",o]
    cmd+=[ROOT/row["path"]]
    shims=ROOT/"deps/wire-shim"
    shims.mkdir(exist_ok=True)
    cvc=shims/"cvc5"
    if not cvc.exists():
        try:
            cvc.symlink_to(ROOT/"scripts/wire-shim.sh")
        except FileExistsError:
            pass
    env=environment(PATH=str(shims)+":/usr/bin:/bin",PYTHONPATH=str(ROOT/"deps/yosys/usr/share/yosys"),
                    WIRE_IN=str(wire/"wire.smt2"),WIRE_OUT=str(wire/"wire-replies.txt"),WIRE_SOLVER=str(CVC))
    process=run(wire,cmd,cpu,cap,env)
    result=dict(id=row["id"],expected=row["expected"],family=row["family"],start=process["start"],stop=process["stop"],
                cpu=cpu,flags=opts,live_ret=process["ret"],live_timeout=process["timeout"])
    if not process["timeout"] and process["ret"] in (0,1) and (wire/"wire.smt2").is_file():
        wire_forms=commands(wire/"wire.smt2",False)
        dump_forms=commands(wire/"dump.smt2",True)
        result["wire_dump_same_commands"]=wire_forms==dump_forms
        result["wire_in_sha256"]=sha(wire/"wire.smt2")
        result["wire_out_sha256"]=sha(wire/"wire-replies.txt")
        result["commands"]=len(wire_forms)
        cvc_answers=ANSWER.findall((wire/"wire-replies.txt").read_text())
        same=folder/"proof-z3"
        z=run(same,[Z3,"-smt2",wire/"wire.smt2"],cpu,cap,environment())
        z3_answers=ANSWER.findall((same/"stdout").read_text())
        result.update(cvc5=cvc_answers,z3=z3_answers,z3_start=z["start"],z3_stop=z["stop"],z3_timeout=z["timeout"],z3_ret=z["ret"],
                      same_answers=cvc_answers==z3_answers and len(z3_answers)>0 and z["ret"]==0 and not z["timeout"])
        if (wire/"sat.vcd").exists():
            result["witness"]=check(wire/"sat.vcd",row["family"],row["width"],row.get("mutation_trigger"))
    write(out,result)
    return result


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("part")
    ap.add_argument("--profile",default="base")
    ap.add_argument("--ids",nargs="+")
    ap.add_argument("--flag",action="append",default=[])
    ap.add_argument("--tag",default="stock")
    ap.add_argument("--cap",type=float,default=35.)
    ap.add_argument("--cpus",nargs="+",type=int,default=CPUS)
    a=ap.parse_args()
    rows=[i for i in manifest(a.part,"hw",a.profile) if "error" not in i and (not a.ids or i["id"] in a.ids)]
    outdir=ROOT/"raw"/f"hw-wire-{a.profile}-{a.part}-{a.tag}"
    def one_core(cpu):
        return [audit(row,cpu,a.cap,outdir,a.flag) for i,row in enumerate(rows) if a.cpus[i%len(a.cpus)]==cpu]
    with ThreadPoolExecutor(max_workers=len(a.cpus)) as pool:
        out=[r for group in pool.map(one_core,a.cpus) for r in group]
    write(outdir/"rows.json",out)
    print(now(),len(out),sum(r.get("same_answers",False) for r in out),flush=True)
