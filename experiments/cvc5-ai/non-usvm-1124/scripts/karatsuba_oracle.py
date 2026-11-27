"""Exhaustively simulate the held-out Karatsuba case whose Z3 run reached its cap."""
from pathlib import Path
import re
import shutil

from common import ROOT, now, read, run, sha, write
from eval import manifest

lock=ROOT/"evidence/hw-search-lock.json"
assert lock.is_file()
row=next(r for r in manifest("test","hw","base") if r["id"]=="karatsuba-w10-ok")
src=(ROOT/row["path"]).with_name("design.v")
assert sha(src)==row["source_sha256"]
n=row["width"]
count=1<<n
out=ROOT/"raw/karatsuba-exhaustive"
out.mkdir(parents=True,exist_ok=True)
sim=out/"design.sv"
original=src.read_text()
escaped=re.sub(r"\bref\b", lambda _:r"\ref ",original)
assert escaped.replace(r"\ref ","ref")==original
sim.write_text(escaped)
tb=out/"tb.sv"
tb.write_text(f"""module tb;
reg [{n-1}:0] a, b, c = 0;
reg [4:0] shifts = 0;
top dut(a,b,c,shifts);
integer i,j, checked;
initial begin
    checked = 0;
    for(i = 0; i < {count}; i = i + 1) begin
        for(j = 0; j < {count}; j = j + 1) begin
            a = i;
            b = j;
            #1;
            if (dut.\\ref  !== (i*j) || dut.got !== (i*j))
                $fatal(1, "mismatch %0d %0d %0d %0d", i, j, dut.\\ref , dut.got);
            checked = checked + 1;
        end
    end
    $display("EXHAUSTIVE PASS %0d", checked);
    $finish;
end
endmodule
""")
compiler=shutil.which("iverilog")
runner=shutil.which("vvp")
assert compiler and runner
binary=out/"top.vvp"
identity=run(out/"version",[compiler,"-V"],12,4.)
build=run(out/"compile",[compiler,"-g2012","-gassertions","-s","tb","-o",binary,sim,tb],12,8.)
assert build["ret"]==0, (out/"compile/stderr").read_text()
exercise=run(out/"exercise",[runner,binary],12,38.)
stdout=(out/"exercise/stdout").read_text()
confirmed=exercise["ret"]==0 and f"EXHAUSTIVE PASS {count**2}" in stdout
write(out/"summary.json",dict(at=now(),id=row["id"],start=exercise["start"],stop=exercise["stop"],
                            expected="unsat",complete=confirmed,designed_vectors=count**2,
                            input_sha256=sha(src),simulation_source_sha256=sha(sim),testbench_sha256=sha(tb),result_sha256=sha(out/"exercise/stdout"),
                            tool_version=(out/"version/stdout").read_text().splitlines()[:1]))
print(now(),confirmed,count**2,flush=True)
assert confirmed
