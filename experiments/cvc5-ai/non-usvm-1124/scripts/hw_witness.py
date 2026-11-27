"""Independently evaluate the first failing RTL witness at BMC step zero."""
from pathlib import Path
import re

DECL=re.compile(r"\$var (?:wire|reg|integer) (\d+) (\S+) (\S+) \$end")
TRIGGER=re.compile(r"a\[(\d+)\](?: && b\[0\])?")


def first_vcd(path):
    symbols={}
    values={}
    t=None
    for line in path.read_text().splitlines():
        decl=DECL.fullmatch(line)
        if decl:
            symbols[decl[2]]=(int(decl[1]),decl[3])
        elif line.startswith("#"):
            t=int(line[1:])
            if t>0:
                break
        elif t==0 and line.startswith("b"):
            val,key=line[1:].split(maxsplit=1)
            if key in symbols and all(c in "01" for c in val):
                size,name=symbols[key]
                values[name]=(int(val,2),size)
    return values


def signed(x,width):
    return x-(1<<width) if x>>(width-1) else x


def check(path,family,width,trigger=None):
    observed=first_vcd(path)
    get=lambda x:observed[x][0]
    n=width
    ref,got=get("ref"),get("got")
    a=observed.get("a",(0,0))[0]
    b=observed.get("b",(0,0))[0]
    c=observed.get("c",(0,0))[0]
    run=TRIGGER.fullmatch(trigger or "a[0]")
    mutation_trigger=int(bool((a>>int(run[1]))&1)) if run else 1
    if family in {"unsigned-divider","signed-remainder"}:
        if not b:
            return dict(ok=False,reason="zero divisor")
        mutation_trigger &= b&1
    if "inc" in observed:
        mutation_trigger=get("inc")
    if family in {"schoolbook","karatsuba"}:
        expected=a*b
    elif family=="signed-product":
        expected=signed(a,n)*signed(b,n)
    elif family=="unsigned-divider":
        expected=a//b
    elif family=="signed-remainder":
        x,y=signed(a,n),signed(b,n)
        expected=(-1 if x<0 else 1)*(abs(x)%abs(y))
    elif family=="popcount":
        expected=a.bit_count()
    elif family=="carry-lookahead":
        expected=a+b+(c&1)
    elif family=="sort-network":
        l,m,h=sorted([a,b,c])
        expected=(l<<(2*n))|(m<<n)|h if observed["ref"][1]==3*n else m
    elif family=="median":
        expected=sorted([a,b,c])[1]
    elif family=="priority-index":
        expected=a.bit_length()
    elif family=="prefix-compare":
        expected=int(a>=b)
    elif family=="rotate":
        k=get("shifts")&(n-1)
        expected=(a<<k)|(a>>(n-k))
    elif family=="fair-arbiter":
        r=get("request")
        expected=r&-r
    elif family=="elastic-pipe":
        expected=1<<(n+1)
    elif family in {"phase-counter","gray-counter","modular-counter","serializer","history-ring","pulse-stretcher","window-voter","two-lane-reducer","vector-debounce"}:
        expected=0
    else:
        raise ValueError(f"unimplemented witness for family {family}")
    expected &= (1<<observed["ref"][1])-1
    mutated=got^mutation_trigger
    return dict(ok=ref==got==expected and mutated!=ref,ref=ref,implementation=got,model=expected,mutated=mutated,
                mutation_trigger=mutation_trigger,first_step=0)


if __name__=="__main__":
    import argparse, json
    p=argparse.ArgumentParser()
    p.add_argument("path",type=Path)
    p.add_argument("family")
    p.add_argument("width",type=int)
    p.add_argument("--trigger")
    a=p.parse_args()
    data=check(a.path,a.family,a.width,a.trigger)
    print(json.dumps(data))
    if not data["ok"]:
        raise SystemExit(1)
