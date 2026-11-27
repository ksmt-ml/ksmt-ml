"""Deterministic ordinary Java arithmetic families with exhaustive input oracles."""
from pathlib import Path
import random

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"src/programs"
FAMILIES=("partial","division","carry","overwrite","signed","packing")

def body(family,width,k):
    m=(1<<width)-1
    bits=width//2
    start=f"int a = (x ^ {k}) & {m};\n        int b = (y + {k}) & {m};"
    if family=="partial":
        core=f"""int p = a * b;
        int split = (a & {(1<<bits)-1}) * b + (((a >>> {bits}) * b) << {bits});
        if (split != p) return -1;
        if (p == 0) return 0;
        if ((p & {m}) > {m//2}) return 3;
        return (p & 1) == 0 ? 1 : 2;"""
    elif family=="division":
        core=f"""int d = b | 1;
        int q = a / d;
        int r = a % d;
        if (q * d + r != a || r >= d) return -1;
        if (q == 0) return 0;
        if (r == 0) return 1;
        return (r & {1<<(bits-1)}) == 0 ? 2 : 3;"""
    elif family=="carry":
        core=f"""int p = a + b;
        if (p != (a ^ b) + ((a & b) << 1)) return -1;
        if (p == 0) return 0;
        if (p > {m}) return 3;
        return (p & 1) == 0 ? 1 : 2;"""
    elif family=="overwrite":
        core="""int i = a & 3, j = b & 3;
        int[] table = new int[4];
        table[i] = a + 1;
        table[j] = b + 1;
        if (table[j] != b + 1) return -1;
        if (i == j) {
            if (table[i] != b + 1) return -1;
            return (a & 4) == 0 ? 0 : 1;
        }
        if (table[i] != a + 1) return -1;
        return (b & 4) == 0 ? 2 : 3;"""
    elif family=="signed":
        core=f"""int n = ((a << {32-width}) >> {32-width});
        int q = a < {1 << (width-1)} ? a : a - {1<<width};
        if (n != q) return -1;
        if (n == 0) return 0;
        if (b == 0) return 1;
        return n > b ? 2 : n < 0 ? 3 : 4;"""
    elif family=="packing":
        core=f"""int v = a | (b << {width});
        int a2 = v & {m};
        int b2 = (v >>> {width}) & {m};
        if (a != a2 || b != b2) return -1;
        if (v == 0) return 0;
        if (a == b) return 3;
        return a > b ? 1 : 2;"""
    else:
        raise ValueError(family)
    return start+"\n        "+core

def main():
    OUT.mkdir(exist_ok=True)
    rng=random.Random(231111)
    definitions=[]
    for family in FAMILIES:
        for no in range(1,5):
            w=6+(no+1)%3
            k=rng.randrange(1,1<<w)
            name=family.title()+str(no)
            text=f"""package examples.kernels;
public final class {name} {{
    public static int run(int x, int y) {{
        {body(family,w,k)}
    }}
}}
"""
            (OUT/(name+".java")).write_text(text)
            definitions.append((family,name,w,k))
    oracle_lines=''.join(f'        verify("{fam}", "{name}", {w}, {k});\n' for fam,name,w,k in definitions)
    (OUT/"Oracle.java").write_text("""package examples.kernels;
import java.util.TreeSet;
import java.util.stream.Collectors;

public final class Oracle {
    private static void verify(String group, String name, int w, int k) throws Exception {
        Class<?> cls = Class.forName("examples.kernels." + name);
        var method = cls.getMethod("run", int.class, int.class);
        var values = new TreeSet<Integer>();
        int stop = 1 << w;
        for (int x = 0; x < stop; x++) {
            for (int y = 0; y < stop; y++) {
                int actual = (Integer) method.invoke(null,x,y);
                if (actual == -1) throw new IllegalStateException(name + " violated invariant");
                values.add(actual);
            }
        }
        System.out.printf("%s\t%s\t%d\t%d\t%s%n", group,name,w,k,
                values.stream().map(String::valueOf).collect(Collectors.joining(",")));
    }
    public static void main(String[] args) throws Exception {
"""+oracle_lines+"    }\n}\n")
    parent=ROOT.parent/"oracle-live/src/LiveOracle.kt"
    driver=parent.read_text().replace('tag.startsWith("ieee/")','tag.startsWith("kernel/")')
    driver=driver.replace('target.invoke(null, test.before.parameters[0])','target.invoke(null, *test.before.parameters.toTypedArray())')
    assert driver!=parent.read_text()
    (ROOT/"src/LiveOracle.kt").write_text(driver)

if __name__=="__main__":
    main()
