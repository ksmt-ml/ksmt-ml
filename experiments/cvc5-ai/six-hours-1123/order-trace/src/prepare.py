"""Add multiset traces and a research-only ordering option to the trace overlay."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIOR = ROOT.parent / "trace-cause/src"


def replace(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def prepare():
    trace = (PRIOR / "WorkTrace.kt").read_text()
    trace = replace(trace, "import java.util.HexFormat", "import java.util.Arrays\nimport java.util.HexFormat")
    trace = replace(trace, '    val delayNs', '''    val sortInput = System.getenv("SMT_SORT_INPUT") == "1"
    private var sortedLists = 0L
    private var reorderedLists = 0L
    private var sortNs = 0L
    private var unorderedQueryChain = ByteArray(32)
    private val unorderedPrefixes = ArrayList<String>()
    private val compareAtoms = Comparator<ByteArray> { a, b -> Arrays.compareUnsigned(a, b) }
    val delayNs''')
    trace = replace(trace, "        queryInputs.clear()", '''        queryInputs.clear()
        unorderedPrefixes.clear()
        unorderedQueryChain = zero
        sortedLists = 0
        reorderedLists = 0
        sortNs = 0''')
    trace = replace(trace, '    private fun event(label:', '''    fun <E : KExpr<*>> ordered(xs: List<E>): List<E> {
        if (!sortInput || xs.size < 2) return xs
        val from = System.nanoTime()
        val result = xs.sortedWith { a, b -> compareAtoms.compare(atom(a), atom(b)) }
        sortedLists++
        if (result != xs) reorderedLists++
        sortNs += System.nanoTime() - from
        return result
    }

    private fun event(label:''')
    trace = replace(trace, '        opChain = extension(opChain, "check:${args.size}", args)', '''        unorderedQueryChain = extension(unorderedQueryChain, "assertions:${assertions.size}", assertions.sortedWith(compareAtoms))
        unorderedQueryChain = extension(unorderedQueryChain, "assumptions:${args.size}", args.sortedWith(compareAtoms))
        if (unorderedPrefixes.size < 512) unorderedPrefixes.add(hex.formatHex(unorderedQueryChain))
        opChain = extension(opChain, "check:${args.size}", args)''')
    trace = replace(trace, '''                "\\\"query_sha256\\\":\\\"${hex.formatHex(queryChain)}\\\"," +''', '''                "\\\"query_sha256\\\":\\\"${hex.formatHex(queryChain)}\\\"," +
                "\\\"unordered_query_sha256\\\":\\\"${hex.formatHex(unorderedQueryChain)}\\\"," +
                "\\\"unordered_query_prefix_sha256\\\":${unorderedPrefixes.joinToString(prefix = "[", postfix = "]") { "\\\"$it\\\"" }}," +
                "\\\"sort_input\\\":$sortInput,\\\"sorted_lists\\\":$sortedLists,\\\"reordered_lists\\\":$reorderedLists,\\\"sort_ms\\\":${sortNs / 1e6}," +''')
    (ROOT / "src/WorkTrace.kt").write_text(trace)

    timed = (PRIOR / "ClientStats.kt").read_text()
    for method, label in (("assert", "assert-list"), ("assertAndTrack", "track-list")):
        source = f'''    override fun {method}(exprs: List<KExpr<KBoolSort>>) {{
        val t0 = System.nanoTime()
        if (WorkTrace.enabled) WorkTrace.solver("{label}", exprs)
        delegate.{method}(exprs)'''
        revised = f'''    override fun {method}(exprs: List<KExpr<KBoolSort>>) {{
        val ordered = WorkTrace.ordered(exprs)
        val t0 = System.nanoTime()
        if (WorkTrace.enabled) WorkTrace.solver("{label}", ordered)
        delegate.{method}(ordered)'''
        timed = replace(timed, source, revised)
    assert timed.count("        stack += exprs") == 2
    timed = timed.replace("        stack += exprs", "        stack += ordered")
    timed = replace(timed, '''        return timedCheck(assumptions) { delegate.checkWithAssumptions(assumptions, timeout) }''',
                    '''        val ordered = WorkTrace.ordered(assumptions)
        return timedCheck(ordered) { delegate.checkWithAssumptions(ordered, timeout) }''')
    (ROOT / "src/ClientStats.kt").write_text(timed)


if __name__ == "__main__":
    prepare()
