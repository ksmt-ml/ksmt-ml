"""Fresh sequential validation architectures for the frozen HW optimizer."""
import rtl


def reducer(n):
    return [
        f"module top(input clk, reset, inc, lane, input [{n-1}:0] payload);",
        f"reg [{n-1}:0] whole=0, even_sum=0, odd_sum=0;",
        "always @(posedge clk) begin",
        "  if(reset) begin whole<=0; even_sum<=0; odd_sum<=0; end",
        "  else if (inc) begin",
        "    whole <= whole+payload;",
        "    if(lane) odd_sum<=odd_sum+payload; else even_sum<=even_sum+payload;",
        "  end",
        "end",
        f"wire [{n-1}:0] ref = whole;",
        f"wire [{n-1}:0] got = even_sum+odd_sum;",
    ]


def debounce(n):
    zeros=f"{n}'b0"
    return [
        f"module top(input clk, reset, inc, input [{n-1}:0] samples);",
        f"reg [{n-1}:0] old=0, prior=0, out_ref=0, out_got=0;",
        f"reg [{n-1}:0] masked_old={n}'h{(1<<n)-1:x};",
        "always @(posedge clk) begin",
        f"  if(reset) begin old<=0; prior<=0; out_ref<=0; out_got<=0; masked_old<=~{zeros}; end",
        "  else if (inc) begin",
        "    old<=samples; masked_old<=~samples;",
        "    prior<=old;",
        "    out_ref<= (out_ref & (prior ^ old)) | (old & prior);",
        "    out_got<= (out_got & (prior ^ ~masked_old)) | ~(masked_old | ~prior);",
        "  end",
        "end",
        f"wire [{n-1}:0] ref=out_ref;",
        f"wire [{n-1}:0] got=out_got;",
    ]


def build():
    from common import CPUS
    rtl.FAMILIES["validation"]=("gray-counter","two-lane-reducer","vector-debounce")
    rtl.SEQ_GENERATE.update({"two-lane-reducer":reducer,"vector-debounce":debounce})
    rtl.SEQUENTIAL.update({"two-lane-reducer","vector-debounce"})
    rtl.WIDTH.update({"two-lane-reducer":(4,6),"vector-debounce":(4,7)})
    return rtl.build("validation","base",CPUS[0])
