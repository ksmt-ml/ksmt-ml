"""Preserve the original live solver and capture its typed public KSMT calls."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIOR = ROOT.parent / "followup/src"


def replace(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def prepare():
    text = (PRIOR / "ClientStats.kt").read_text()
    text = replace(text, "    private val stack = ArrayList<KExpr<KBoolSort>>()", '''    private val shadow = ShadowCapture.create()
    private val stack = ArrayList<KExpr<KBoolSort>>()''')
    for method, operation in (("assert", "ASSERT"), ("assertAndTrack", "TRACK")):
        for expr, values in (("expr", "listOf(expr)"), ("exprs", "exprs")):
            call = f"        delegate.{method}({expr})"
            text = replace(text, call, call + f"\n        shadow?.add(ShadowOp.{operation}, {values})")
    text = replace(text, "        delegate.push()", "        delegate.push()\n        shadow?.add(ShadowOp.PUSH)")
    text = replace(text, "        delegate.pop(n)", "        delegate.pop(n)\n        shadow?.add(ShadowOp.POP, depth = n)")
    text = replace(text, "timedCheck(emptyList()) {", "timedCheck(ShadowOp.CHECK, emptyList()) {")
    text = replace(text, "return timedCheck(assumptions) {", "return timedCheck(ShadowOp.ASSUME, assumptions) {")
    text = replace(text, "private fun timedCheck(assumptions:", "private fun timedCheck(op: ShadowOp, assumptions:")
    text = replace(text, "        WorkTrace.afterCheck(status.name)",
                   "        WorkTrace.afterCheck(status.name)\n        shadow?.add(op, assumptions, result = status)")
    text = replace(text, "        return delegate.model()",
                   "        return delegate.model().also { shadow?.add(ShadowOp.MODEL) }")
    text = replace(text, '        WorkTrace.solver("core-result", c)',
                   '        WorkTrace.solver("core-result", c)\n        shadow?.add(ShadowOp.CORE)')
    (ROOT / "src/ClientStats.kt").write_text(text)

    driver = (PRIOR / "PowerDriver.kt").read_text()
    driver = replace(driver, "import org.usvm.solver.ClientStats",
                           "import org.usvm.solver.ClientStats\nimport org.usvm.solver.ShadowCapture")
    driver = replace(driver, "    ClientStats.reset()", '''    ClientStats.reset()
    ShadowCapture.begin(tag.takeUnless { it.startsWith("warm/") })''')
    driver = replace(driver, "            count = machine.analyze(m).size\n            analysis = System.nanoTime() - t0", '''            try {
                count = machine.analyze(m).size
            } finally {
                analysis = System.nanoTime() - t0
                ShadowCapture.finish()
            }''')
    driver = replace(driver, "    val wall = (System.nanoTime() - start) / 1e6", '''    ShadowCapture.begin(null)
    val wall = (System.nanoTime() - start) / 1e6''')
    (ROOT / "src/PowerDriver.kt").write_text(driver)


if __name__ == "__main__":
    prepare()
