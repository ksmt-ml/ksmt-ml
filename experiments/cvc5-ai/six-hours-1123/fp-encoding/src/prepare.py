"""Patch the USVM approximation only in the campaign's isolated experimental overlay."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base

def main():
    a=base.OLD/"checkout/usvm/usvm-jvm/src/main/kotlin/org/usvm/machine/JcApproximations.kt"
    file=ROOT/"src/JcApproximations.kt"
    text=a.read_text()
    for fp,bv in (("floatSort","integerSort"),("doubleSort","longSort")):
        line=f'            val result = ctx.mkFpToIEEEBvExpr(value).asExpr(ctx.{bv})'
        injected=f'''            if (System.getenv("SMT_TEST_FP_INVERSE") == "1") {{
                val result = scope.calcOnState {{ makeSymbolicPrimitive(ctx.{bv}) }}
                val valueRebuilt = mkFpFromBits(ctx.{fp}, result)
                scope.assert(ctx.mkEq(valueRebuilt, value)) {{
                    skipMethodInvocationWithValue(methodCall, result)
                }}
                return true
            }}
'''
        assert text.count(line)==1
        text=text.replace(line,injected+line)
    file.write_text(text)
    base.write(ROOT/"evidence/source-sha256.json",dict(original=base.sha(a),overlay=base.sha(file),at=base.now()))

if __name__=="__main__":
    main()
