"""Generate newly authored hardware and compile using real Yosys."""
from pathlib import Path
import argparse
import random
from common import ROOT, YOSYS, now, run, sha, stable_seed, write, read
from sequential_rtl import GENERATE as SEQ_GENERATE, SIZES as SEQ_WIDTH

# Independent design archetypes. Variant widths and mutations within one
# archetype remain in one source cluster.
FAMILIES = {
    "train": ("schoolbook", "unsigned-divider", "popcount", "phase-counter", "carry-lookahead", "sort-network"),
    "validation": ("signed-product", "priority-index", "gray-counter"),
    "test": ("karatsuba", "signed-remainder", "rotate", "modular-counter", "prefix-compare",
             "elastic-pipe", "fair-arbiter", "window-voter"),
}
# Profiles extend only TRAIN. Validation and test use prespecified base sizes.
WIDTH = {
    "schoolbook": (7, 9), "unsigned-divider": (8, 11), "popcount": (24, 39),
    "phase-counter": (5, 7), "carry-lookahead": (17, 25), "sort-network": (4, 6),
    "signed-product": (7, 9), "priority-index": (30, 45), "gray-counter": (5, 7),
    "karatsuba": (8, 10), "signed-remainder": (8, 10), "rotate": (16, 32),
    "modular-counter": (5, 7), "prefix-compare": (17, 25), "median": (5, 8),
}
SEQUENTIAL = {"phase-counter", "gray-counter", "modular-counter", *SEQ_GENERATE}
WIDTH.update(SEQ_WIDTH)


def combinational(family, n, profile):
    common = f"module top(input [{n-1}:0] a, b, c, input [4:0] shifts);"
    lines = [common]
    def wire(width, name, expr):
        lines.append(f"wire [{width-1}:0] {name} = {expr};")
    if family in ("schoolbook", "signed-product", "karatsuba"):
        w = 2*n
        if family == "signed-product":
            wire(w, "ref", "$signed(a) * $signed(b)")
            wire(w, "mul_a", "{{" + str(n) + "{a[" + str(n-1) + "]}}, a}")
        else:
            wire(w, "ref", "a * b")
            wire(w, "mul_a", "a")
        if family == "karatsuba":
            split = n//2
            hi = n-split
            wire(w, "al", f"a[{split-1}:0]")
            wire(w, "bl", f"b[{split-1}:0]")
            wire(w, "ah", f"a[{n-1}:{split}]")
            wire(w, "bh", f"b[{n-1}:{split}]")
            wire(w, "zlo", "al * bl")
            wire(w, "zhi", "ah * bh")
            wire(w, "zmix", "(al + ah) * (bl + bh) - zlo - zhi")
            wire(w, "got", f"(zhi << {2*split}) + (zmix << {split}) + zlo")
        else:
            for i in range(n):
                wire(w, f"term{i}", f"b[{i}] ? (mul_a << {i}) : {w}'b0")
            terms = [f"term{i}" for i in range(n)]
            sign = terms.pop() if family == "signed-product" else None
            while len(terms) > 1:
                first, second, *terms = terms
                name = f"sum_{len(lines)}"
                wire(w, name, f"{first} + {second}")
                terms.append(name)
            wire(w, "got", terms[0] + (f" - {sign}" if sign else ""))
    elif family in ("unsigned-divider", "signed-remainder"):
        signed = family == "signed-remainder"
        wire(n, "x", f"a[{n-1}] ? -a : a" if signed else "a")
        wire(n, "d", f"b[{n-1}] ? -b : b" if signed else "b")
        wire(n+1, f"r{n}", "0")
        for i in reversed(range(n)):
            wire(n+1, f"s{i}", f"{{r{i+1}[{n-1}:0], x[{i}]}}")
            wire(1, f"q{i}", f"s{i} >= {{1'b0,d}}")
            wire(n+1, f"r{i}", f"q{i} ? s{i} - {{1'b0,d}} : s{i}")
        if signed:
            wire(n, "ref", "$signed(a) % $signed(b)")
            wire(n, "got", f"a[{n-1}] ? -r0[{n-1}:0] : r0[{n-1}:0]")
        else:
            wire(n, "ref", "a / b")
            wire(n, "got", "{" + ",".join(f"q{i}" for i in reversed(range(n))) + "}")
        return lines, "b != 0"
    elif family == "popcount":
        w = n.bit_length()
        wire(w, "ref", " + ".join(f"a[{i}]" for i in range(n)))
        for i in range(0,n,3):
            wire(w, f"cnt{i}", " + ".join(f"a[{j}]" for j in range(i,min(n,i+3))))
        wire(w, "got", " + ".join(f"cnt{i}" for i in range(0,n,3)))
    elif family == "carry-lookahead":
        wire(n+1, "ref", "a + b + c[0]")
        wire(1, "carry0", "c[0]")
        for i in range(n):
            wire(1, f"p{i}", f"a[{i}] ^ b[{i}]")
            wire(1, f"g{i}", f"a[{i}] & b[{i}]")
            base = (i//4)*4
            chain = [f"(g{j}" + "".join(f" & p{k}" for k in range(j+1,i+1)) + ")" for j in range(base,i+1)]
            chain.append(f"(carry{base}" + "".join(f" & p{k}" for k in range(base,i+1)) + ")")
            wire(1, f"carry{i+1}", " | ".join(chain))
            wire(1, f"sum{i}", f"p{i} ^ carry{i}")
        wire(n+1, "got", "{" + ",".join([f"carry{n}",*(f"sum{i}" for i in reversed(range(n)))])+"}")
    elif family=="sort-network" and profile!="probe":
        # Two different comparator networks produce all three sorted keys.
        wire(n, "lower_ab", "a < b ? a : b")
        wire(n, "upper_ab", "a < b ? b : a")
        wire(n, "lower_bc", "b < c ? b : c")
        wire(n, "upper_bc", "b < c ? c : b")
        wire(n, "lo_ref", "lower_ab < c ? lower_ab : c")
        wire(n, "hi_ref", "upper_ab > c ? upper_ab : c")
        wire(n, "lo_got", "a < lower_bc ? a : lower_bc")
        wire(n, "hi_got", "a > upper_bc ? a : upper_bc")
        wire(n, "mid_ref", "a ^ b ^ c ^ lo_ref ^ hi_ref")
        wire(n, "mid_got", "a < lower_bc ? lower_bc : (a > upper_bc ? upper_bc : a)")
        wire(3*n, "ref", "{lo_ref, mid_ref, hi_ref}")
        wire(3*n, "got", "{lo_got, mid_got, hi_got}")
    elif family in ("sort-network", "median"):
        # Three-input numerical median identity, using opposite comparator networks.
        wire(n, "lo1", "a < b ? a : b")
        wire(n, "hi1", "a < b ? b : a")
        wire(n, "ref", "c < lo1 ? lo1 : (c > hi1 ? hi1 : c)")
        if family == "sort-network":
            wire(n, "lo2", "b < c ? b : c")
            wire(n, "hi2", "b < c ? c : b")
            wire(n, "got", "a < lo2 ? lo2 : (a > hi2 ? hi2 : a)")
        else:
            wire(n, "lo2", "a < c ? a : c")
            wire(n, "hi2", "a < c ? c : a")
            wire(n, "got", "b > hi2 ? hi2 : (b > lo2 ? b : lo2)")
    elif family == "priority-index":
        w = n.bit_length()
        expr = f"{w}'d0"
        for i in range(n):
            expr = f"a[{i}] ? {w}'d{i+1} : ({expr})"
        wire(w, "ref", expr)
        for i in range(n):
            higher = f"a[{n-1}:{i+1}] == 0 && " if i<n-1 else ""
            wire(w, f"sel{i}", f"({higher}a[{i}]) ? {w}'d{i+1} : {w}'d0")
        wire(w, "got", " | ".join(f"sel{i}" for i in range(n)))
    elif family == "rotate":
        bits=(n-1).bit_length()
        wire(n, "ref", f"(a << shifts[{bits-1}:0]) | (a >> ({n} - shifts[{bits-1}:0]))")
        prev="a"
        for i in range(bits):
            k=1<<i
            wire(n, f"rot{i}", f"shifts[{i}] ? {{{prev}[{n-k-1}:0], {prev}[{n-1}:{n-k}]}} : {prev}")
            prev=f"rot{i}"
        wire(n, "got", prev)
    elif family == "prefix-compare":
        wire(1, "ref", "a >= b")
        for i in range(n):
            prefix = f"(a[{n-1}:{i+1}] == b[{n-1}:{i+1}]) && " if i<n-1 else ""
            wire(1, f"gt{i}", f"({prefix}a[{i}] && !b[{i}])")
        wire(1, "got", "(a == b) | " + " | ".join(f"gt{i}" for i in range(n)))
    else:
        raise ValueError(family)
    return lines, "1'b1"


def sequential(family,n,rng,profile):
    limit=(1<<n)-rng.randint(3,7)
    lines=[f"module top(input clk, input inc, reset);",
           f"reg [{n-1}:0] spec = 0, impl = 0;",
           "always @(posedge clk) begin"]
    if family=="gray-counter":
        lines += ["if (reset) begin spec <= 0; impl <= 0; end",
                  "else if (inc) begin spec <= spec + 1; impl <= spec + 1 ^ ((spec + 1) >> 1); end",
                  "end", f"wire [{n-1}:0] ref = spec ^ (spec >> 1);",
                  f"wire [{n-1}:0] got = impl;"]
        # Use width-matched wraparound before converting next count to Gray.
        lines[4] = f"else if (inc) begin spec <= nxt; impl <= nxt ^ (nxt >> 1); end"
        lines.insert(2,f"wire [{n-1}:0] nxt = spec + 1'b1;")
    elif family=="modular-counter":
        lines += [f"if (reset) begin spec <= 0; impl <= {limit}; end",
                  f"else if (inc) begin spec <= spec == {limit} ? 0 : spec + 1'b1;",
                  f"impl <= impl == 0 ? {limit} : impl - 1'b1; end", "end",
                  f"wire [{n-1}:0] ref = spec;",
                  f"wire [{n-1}:0] got = {n}'d{limit} - impl;"]
        lines[1] = f"reg [{n-1}:0] spec = 0, impl = {limit};"
    elif profile!="probe":
        lines[0]="module top(input clk, input inc, dec, reset);"
        lines += [f"if (reset) begin spec <= 0; impl <= 0; end",
                  f"else begin",
                  f"if (inc && !dec && spec != {limit}) spec <= spec + 1'b1;",
                  "if (dec && !inc && spec != 0) spec <= spec - 1'b1;",
                  f"case ({{inc,dec}}) 2'b10: impl <= impl >= {limit} ? {limit} : impl + 1'b1;",
                  "2'b01: impl <= impl == 0 ? 0 : impl - 1'b1; default: impl <= impl; endcase",
                  "end end",f"wire [{n-1}:0] ref = spec;",f"wire [{n-1}:0] got = impl;"]
    else:
        lines += [f"if (reset) begin spec <= 0; impl <= 0; end",
                  f"else if (inc) begin spec <= spec == {limit} ? 0 : spec + 1'b1;",
                  f"impl <= (impl + 1'b1 == {n}'d{limit+1}) ? 0 : impl + 1'b1; end", "end",
                  f"wire [{n-1}:0] ref = spec;",
                  f"wire [{n-1}:0] got = impl;"]
    return lines, "1'b1"


def build(part, profile, cpu, release=None):
    assert part == "train" or profile == "base"
    if part == "test":
        assert release and (ROOT / "evidence" / "hw-test-release.json").is_file()
    out = ROOT / "inputs" / profile / part / "hw"
    marker = out / "manifest.json"
    if marker.exists():
        return read(marker)
    rows=[]
    families=("phase-counter","serializer","history-ring","pulse-stretcher") if profile=="seq" else FAMILIES[part]
    for family in families:
        widths=WIDTH[family]
        if profile=="probe":
            widths=(widths[0],)
        if profile=="hard":
            widths=tuple(w+1 for w in widths)
        for j,n in enumerate(widths):
            for mutation in ((False,) if profile=="probe" else ((False, True) if j==0 else (False,))):
                name=f"{family}-w{n}-{'bad' if mutation else 'ok'}"
                rng=random.Random(stable_seed("rtl", family,n,release or 331))
                seq=family in SEQUENTIAL
                if family in SEQ_GENERATE:
                    lines,guard=SEQ_GENERATE[family](n),"1'b1"
                else:
                    lines,guard=sequential(family,n,rng,profile) if seq else combinational(family,n,profile)
                # A known mutation flips bit zero, conditional on a legal witness.
                tick=rng.randrange(n) if part=="test" else 0
                trigger="inc" if seq else (f"a[{tick}]" if family not in ("unsigned-divider","signed-remainder") else f"a[{tick}] && b[0]")
                expr=f"(got ^ (({trigger}) ? 1'b1 : 1'b0))" if mutation else "got"
                if guard!="1'b1":
                    lines.append(f"always @* assume ({guard});")
                lines += [f"always @* assert (ref == {expr});", "endmodule"]
                case=out/name
                case.mkdir(parents=True,exist_ok=True)
                src=case/"design.v"
                src.write_text("\n".join(lines)+"\n")
                smt=case/"netlist.smt2"
                cmd=[YOSYS,"-Q","-T","-p",f"read_verilog -formal {src}; prep -top top; write_smt2 -wires {smt}"]
                status=run(ROOT/"raw/build"/profile/part/"hw"/name,cmd,cpu,12)
                if status["ret"]!=0 or not smt.is_file():
                    rows.append(dict(id=name,family=family,error="yosys",process=status))
                    continue
                depth=1 if not seq else (6 if profile=="probe" else 8+n//2)
                if seq and profile=="seq":
                    depth=10+n//2
                rows.append(dict(id=name,family=family,producer="hw",depth=depth,expected="sat" if mutation else "unsat",
                                 path=str(smt.relative_to(ROOT)),sha256=sha(smt),source_sha256=sha(src),width=n,mutation_trigger=trigger if mutation else None))
    write(marker,dict(at=now(),part=part,profile=profile,items=rows))
    return read(marker)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("part",choices=FAMILIES)
    parser.add_argument("--profile",default="base",choices=("probe","base","hard","seq"))
    parser.add_argument("--cpu",type=int,default=12)
    args=parser.parse_args()
    if args.part=="test":
        release=read(ROOT/"evidence/hw-test-release.json")["nonce"]
    else:
        release=None
    m=build(args.part,args.profile,args.cpu,release)
    print(m["at"],[(row["id"],row.get("error","ok")) for row in m["items"]],flush=True)
