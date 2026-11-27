"""Freeze independent Java algorithm families before any measured tuning."""
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"recovery/scripts"))
import base

PROTOCOL=TOP/"protocols/20-array-array-tuning.md"
FAMILIES={
    "train": {
        "SingleWrite": """int[] t=new int[8]; t[a&7]=1+(a&7);
        int value=t[b&7]; return value==0 ? 0 : value>4 ? 1 : 2;""",
        "ElementSwap": """int[] t=new int[2]; t[0]=a+1; t[1]=b+1;
        int i=a&1,j=b&1,v=t[i]; t[i]=t[j]; t[j]=v;
        if(t[0]+t[1]!=a+b+2) return -1; return i==j ? 0 : t[0]>t[1] ? 1 : 2;""",
        "RecoverZero": """int[] t=new int[4]; int i=a&3; t[i]=(a^b)+1;
        if(t[(i+1)&3]!=0) return -1; return t[i]>16 ? 1 : 2;""",
        "ByteScratch": """byte[] t=new byte[4]; t[a&3]=(byte)(a+b);
        byte v=t[(a^b)&3]; return v==0 ? 0 : v<0 ? 1 : 2;""",
    },
    "validation": {
        "CounterRead": """int[] t=new int[8]; int i=b&7;
        t[i] += a&7; t[(i+2)&7] += b&7;
        if(t[i]!= (a&7)) return -1; return t[(a+b)&7] == 0 ? 0 : 1;""",
        "Bitset": """boolean[] t=new boolean[4]; t[a&3]=true; t[b&3]=true;
        if(!t[a&3] || !t[b&3]) return -1; return t[(a^b)&3] ? 1 : 0;""",
        "MirrorRead": """int[] t=new int[8]; int i=a&3; t[i]=a+1; t[7-i]=b+1;
        if(t[7-i]!=b+1) return -1; return t[b&7]==0 ? 0 : t[b&7]>16 ? 1 : 2;""",
        "WidthControl": """int v=((a&3)==(b&3)) ? a+1 : 0;
        if(v==0) return 0; return v>16 ? 1 : 2;""",
    },
    "test": {
        "SlidingSlot": """int[] t=new int[] {a,b,0,0}; int i=b&1; t[i+2]=t[i]; t[i]=0;
        if(t[i+2] != (i==0 ? a : b)) return -1; return t[a&3]==0 ? 0 : t[a&3]>16 ? 1 : 2;""",
        "CrossRead": """int[] t=new int[4], u=new int[4]; int i=a&3, j=b&3;
        t[i]=a+1; u[j]=b+1; t[j]+=u[i];
        if(t[i]==0 || u[j]==0) return -1; return t[j]==0 ? 0 : t[j]>16 ? 1 : 2;""",
        "BucketDelete": """int[] t=new int[4]; t[a&3]=a+1; t[b&3]=0;
        if(t[b&3]!=0) return -1; return t[a&3]==0 ? 0 : t[a&3]>16 ? 1 : 2;""",
        "LongAccum": """long[] t=new long[4]; int i=a&3,j=b&3;
        t[i]=1L<< (a&31); t[j]+=1L<<(b&31);
        long value=t[(a+b)&3]; return value==0 ? 0 : value>(1L<<16) ? 1 : 2;""",
    },
}


def freeze():
    dest=ROOT/"inputs/design.json"
    if dest.is_file():
        return base.read(dest)
    rng=random.Random(23112001)
    split={}
    calls=[]
    loc=ROOT/"src/programs"
    for key, families in FAMILIES.items():
        split[key]={}
        for name, code in families.items():
            split[key][name]={}
            for v, w in enumerate((6,7),1):
                cls=name+str(v)
                mask=(1<<w)-1
                k=rng.randrange(1,mask)
                path=loc/(cls+".java")
                path.write_text(f'''package examples.kernels;
public final class {cls} {{
    public static int run(int x, int y) {{
        int a=(x^{k})&{mask}, b=(y+{k})&{mask};
        {code}
    }}
}}
''')
                split[key][name][cls]=dict(tag=f"kernel/examples.kernels.{cls}.run(II)I", sha=base.sha(path),
                                           width=w,constant=k)
                calls.append(f'        verify("{name}", "{cls}", {w}, {k});\n')
    head=(TOP/"kernels/src/programs/Oracle.java").read_text().split('        verify("partial',1)[0]
    assert head.endswith("throws Exception {\n")
    (loc/"Oracle.java").write_text(head+"".join(calls)+"    }\n}\n")
    (ROOT/"src/LiveOracle.kt").write_text((TOP/"kernels/src/LiveOracle.kt").read_text())
    d=dict(at=base.now(),protocol=base.sha(PROTOCOL),split=split,oracle_src=base.sha(loc/"Oracle.java"))
    base.write(dest,d)
    return d


if __name__=="__main__":
    print({k:len(v) for k,v in freeze()["split"].items()})
