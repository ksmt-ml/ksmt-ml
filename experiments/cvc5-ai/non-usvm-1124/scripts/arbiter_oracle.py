"""Exhaustive transition and representation check for the five-lane arbiter."""
import re
import shutil

from common import ROOT, now, run, sha, write
from eval import manifest

row=next(x for x in manifest("test","hw","base") if x["id"]=="fair-arbiter-w5-ok")
assert row["width"]==5
source=(ROOT/row["path"]).with_name("design.v")
assert sha(source)==row["source_sha256"]
out=ROOT/"raw/arbiter-exhaustive"
out.mkdir(parents=True,exist_ok=True)
text=source.read_text()
sim=out/"design.sv"
replacement=re.sub(r"\bref\b",lambda _:r"\ref ",text)
assert replacement.replace(r"\ref ","ref")==text
sim.write_text(replacement)
tb=out/"tb.sv"
tb.write_text(r"""module tb;
reg clk=0, reset=0, inc=0;
reg [4:0] request;
top dut(clk,reset,inc,request);
integer h, q, e, r, i, idx, found, nexthead, checked;
reg [4:0] grant, nexttoken;
initial begin
  #1;
  if (dut.head !== 0 || dut.token !== 5'd1) $fatal(1, "initial state");
  checked=0;
  for(h=0;h<5;h=h+1) begin
    for(q=0;q<32;q=q+1) begin
      for(e=0;e<2;e=e+1) begin
        for(r=0;r<2;r=r+1) begin
          clk=0; inc=e; reset=r; request=q;
          dut.head=h;
          dut.token=5'd1 << h;
          grant=0; found=-1;
          for(i=0;i<5;i=i+1) begin
            idx=(h+i)%5;
            if(found<0 && ((q >> idx)&1)) begin
              found=idx; grant=5'd1 << idx;
            end
          end
          #1;
          if(dut.\ref  !== grant || dut.got !== grant) $fatal(1, "grant mismatch h=%d q=%d",h,q);
          nexthead=r ? 0 : (e && found>=0 ? (found+1)%5 : h);
          nexttoken=5'd1 << nexthead;
          clk=1;
          #1;
          if(dut.head !== nexthead || dut.token !== nexttoken)
            $fatal(1, "transition mismatch h=%d q=%d e=%d r=%d",h,q,e,r);
          clk=0;
          #1;
          checked=checked+1;
        end
      end
    end
  end
  $display("TRANSITIONS PASS %0d",checked);
  $finish;
end
endmodule
""")
binary=out/"sim.vvp"
build=run(out/"build",[shutil.which("iverilog"),"-g2012","-gassertions","-s","tb","-o",binary,sim,tb],12,6.)
assert build["ret"]==0,(out/"build/stderr").read_text()
exe=run(out/"exercise",[shutil.which("vvp"),binary],12,8.)
stdout=(out/"exercise/stdout").read_text()
okay=exe["ret"]==0 and "TRANSITIONS PASS 640" in stdout
write(out/"summary.json",dict(at=now(),case=row["id"],start=exe["start"],stop=exe["stop"],transitions=640,complete=okay,
                            source_sha256=sha(source),simulation_source_sha256=sha(sim),testbench_sha256=sha(tb),
                            stdout_sha256=sha(out/"exercise/stdout")))
print(now(),okay,flush=True)
assert okay
