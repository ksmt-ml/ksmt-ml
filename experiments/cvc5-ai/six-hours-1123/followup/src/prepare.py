"""Create isolated sources from byte-for-byte archive inputs."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent / "recovery/scripts"))
import base

def edit(source, old, new, count=1):
    actual = source.count(old)
    assert actual == count, (old, actual, count)
    return source.replace(old, new)

def main():
    src = base.OLD / "checkout/usvm"
    source = src / "usvm-core/src/main/kotlin/org/usvm/solver/ClientStats.kt"
    core = source.read_text()
    core = edit(core, '        reset()\n        return s.dropLast(1) + extra + "}"',
                '        val trace = WorkTrace.json()\n        reset()\n        return s.dropLast(1) + extra + trace + "}"')
    core = edit(core, '    fun reset() {\n', '    fun reset() {\n        WorkTrace.reset()\n')
    for name, arg, expr, kind in (("assert","expr","listOf(expr)","assert"),
                                   ("assert","exprs","exprs","assert-list"),
                                   ("assertAndTrack","expr","listOf(expr)","track"),
                                   ("assertAndTrack","exprs","exprs","track-list")):
        line = f'        delegate.{name}({arg})\n'
        core = edit(core, line, f'        if (WorkTrace.enabled) WorkTrace.solver("{kind}", {expr})\n' + line)
    core = edit(core, '        delegate.push()\n', '        WorkTrace.solver("push")\n        delegate.push()\n')
    core = edit(core, '        delegate.pop(n)\n', '        if (WorkTrace.enabled) WorkTrace.solver("pop:$n")\n        delegate.pop(n)\n')
    core = edit(core, '        val status = run()\n        val d = System.nanoTime() - t0',
                '        WorkTrace.inject()\n        val status = run()\n        val d = System.nanoTime() - t0\n        WorkTrace.afterCheck(status.name)')
    core = edit(core, '        val dupSet = !seenSet.add(all.toHashSet())\n',
                '        val dupSet = !seenSet.add(all.toHashSet())\n        WorkTrace.beforeCheck(assumptions)\n')
    core = edit(core, '        ClientStats.modelCalls++\n',
                '        WorkTrace.solver("model")\n        ClientStats.modelCalls++\n')
    core = edit(core, '        val c = delegate.unsatCore()\n',
                '        val c = delegate.unsatCore()\n        WorkTrace.solver("core-result", c)\n')
    core = edit(core, '    override fun close() = delegate.close()',
                '    override fun close() {\n        WorkTrace.solver("close")\n        delegate.close()\n    }')
    (HERE/"ClientStats.kt").write_text(core)

    jvm = src / "usvm-jvm/src/main/kotlin/org/usvm/machine/JcMachine.kt"
    jvm_code = edit(jvm.read_text(), '                val stmt = state.lastStmt\n',
             '                org.usvm.solver.WorkTrace.state(state.pathNode)\n                val stmt = state.lastStmt\n')
    (HERE/"JcMachine.kt").write_text(jvm_code)
    source = base.BUILD / "src/LiveSearchDriver.kt"
    driver = edit(source.read_text(), "timeout = 6000.milliseconds,",
        'timeout = if (cfg["budget"] == "fixed") kotlin.time.Duration.INFINITE else 6000.milliseconds,')
    driver = edit(driver, "stepLimit = 4000uL,",
        'stepLimit = cfg["steps"]?.toULong() ?: 4000uL,')
    driver = edit(driver, "pathSelectionStrategies = listOf(PathSelectionStrategy.FORK_DEPTH),",
        'pathSelectionStrategies = listOf(cfg["selector"]?.let(PathSelectionStrategy::valueOf) ?: PathSelectionStrategy.FORK_DEPTH),')
    (HERE/"PowerDriver.kt").write_text(driver)

if __name__ == "__main__":
    main()
