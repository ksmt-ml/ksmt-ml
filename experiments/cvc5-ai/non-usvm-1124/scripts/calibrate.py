"""Measure the common process floor on each logical CPU representing a core."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import statistics

from common import ROOT, BMC, CVC, CPUS, now, read, run, environment, write

FOLDER=ROOT/"inputs/calibration"
FOLDER.mkdir(parents=True,exist_ok=True)
STATIC=FOLDER/"minimum.smt2"
STATIC.write_text("(set-logic QF_LIA)\n(check-sat)\n(exit)\n")
HDL=FOLDER/"minimum.v"
HDL.write_text("module top(input a); always @* assert(a == a); endmodule\n")
SMT=FOLDER/"minimum-bmc.smt2"

def minimum_bmc():
    from common import YOSYS
    cmd=[YOSYS,"-Q","-T","-p",f"read_verilog -formal {HDL}; prep -top top; write_smt2 -wires {SMT}"]
    return run(ROOT/"raw/calibrate/build",cmd,12,5.)


def launch(cpu):
    rows={}
    for name in ("exec","static","bmc"):
        xs=[]
        for i in range(20):
            where=ROOT/"raw/calibrate"/str(cpu)/name/f"r{i:02d}"
            env=environment()
            if name=="exec":
                cmd=["/usr/bin/true"]
            elif name=="static":
                cmd=[CVC,"--lang=smt2",STATIC]
            else:
                cmd=[BMC,"-s","cvc5","--noprogress","--presat","--dump-smt2",where/"dump.smt2",
                     "--dummy",where/"replies.txt","-t","1",SMT]
                env=environment(PATH=f"{CVC.parent}:/usr/bin:/bin",PYTHONPATH=str(ROOT/"deps/yosys/usr/share/yosys"))
            r=run(where,cmd,cpu,3.,env)
            data=(where/"stdout").read_text()
            if r["ret"]==0 and (name=="exec" or (name=="static" and data.strip()=="sat")
                                or (name=="bmc" and "Status: PASSED" in data)):
                xs.append(r["seconds"])
        rows[name]=dict(n=len(xs),median=statistics.median(xs) if xs else None,min=min(xs) if xs else None,
                        max=max(xs) if xs else None)
    return str(cpu),rows


if __name__=="__main__":
    assert minimum_bmc()["ret"]==0 and SMT.exists()
    with ThreadPoolExecutor(max_workers=4) as pool:
        data=dict(pool.map(launch,CPUS))
    write(ROOT/"evidence/calibration.json",dict(at=now(),data=data))
