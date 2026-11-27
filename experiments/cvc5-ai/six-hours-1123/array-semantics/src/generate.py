"""Generate six ordinary array and scalar numerical families with concrete Java oracle."""
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/programs"

BODIES = {
    "Sparse": """int i = a & 3;
        int[] data = new int[4];
        data[i] = 1 + (b & 7);
        int here = data[b & 3];
        if (here == 0) return 0;
        if (here != 1 + (b & 7)) return -1;
        return (a & 8) == 0 ? 1 : 2;""",
    "Copy": """int i = a & 3, j = b & 3;
        int[] data = new int[4];
        int[] copy = new int[4];
        data[i] = (a & 15) + 1;
        data[j] = (b & 15) + 1;
        for (int k = 0; k < 4; k++) copy[k] = data[k];
        if (copy[i] != data[i] || copy[j] != data[j]) return -1;
        if (i == j) return 0;
        return copy[i] == copy[j] ? 1 : (copy[i] < copy[j] ? 2 : 3);""",
    "Permute": """int i = a & 3, j = (a + 1) & 3;
        int[] data = new int[4];
        data[i] = a + 1;
        data[j] = b + 1;
        int k = b & 3;
        if (k == i) return data[k] == a + 1 ? 0 : -1;
        if (k == j) return data[k] == b + 1 ? 1 : -1;
        return data[k] == 0 ? 2 : -1;""",
    "Buckets": """int[] data = new int[4];
        int i = (a ^ (a >>> 3)) & 3, j = (b ^ (b >>> 3)) & 3;
        data[i] += a & 7;
        data[j] += b & 7;
        if (data[0] + data[1] + data[2] + data[3] != (a & 7) + (b & 7)) return -1;
        if (data[i] == 0) return 0;
        if (i == j) return data[i] > 7 ? 1 : 2;
        return data[i] > data[j] ? 3 : 4;""",
    "Filled": """int[] data = new int[] {a, b, a ^ b, a + b};
        int i = (a ^ b) & 3, j = (i + 2) & 3;
        data[i] = data[j] + 1;
        if (data[i] != data[j] + 1) return -1;
        if (data[j] == 0) return 0;
        return (data[i] & 1) == 0 ? 1 : 2;""",
    "Scalar": """int i = a & 3, j = b & 3;
        int here = i == j ? (b & 7) + 1 : 0;
        if (here == 0) return 0;
        if (here != (b & 7) + 1) return -1;
        return (a & 8) == 0 ? 1 : 2;""",
}


def main():
    rng = random.Random(23111801)
    lines = []
    for family, body in BODIES.items():
        for i, width in enumerate((5, 6, 7, 6), start=1):
            mask = (1 << width) - 1
            k = rng.randrange(1, mask)
            cls = family + str(i)
            prefix = f"int a = (x ^ {k}) & {mask};\n        int b = (y + {k}) & {mask};"
            (OUT / (cls+".java")).write_text(f'''package examples.kernels;
public final class {cls} {{
    public static int run(int x, int y) {{
        {prefix}
        {body}
    }}
}}
''')
            lines.append(f'        verify("{family.lower()}", "{cls}", {width}, {k});\n')
    source = (ROOT.parent / "kernels/src/programs/Oracle.java").read_text()
    head = source.split('        verify("partial', 1)[0]
    assert head.endswith("throws Exception {\n")
    (OUT / "Oracle.java").write_text(head+"".join(lines)+"    }\n}\n")
    driver = ROOT.parent / "kernels/src/LiveOracle.kt"
    (ROOT / "src/LiveOracle.kt").write_text(driver.read_text())


if __name__ == "__main__":
    main()
