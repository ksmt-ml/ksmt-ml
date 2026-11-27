"""Build a separate JVM driver for balanced settings within one indexed library."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base

def replace(text,old,new):
    assert text.count(old)==1,(old,text.count(old))
    return text.replace(old,new)

def main():
    output=ROOT/"build/src"
    output.mkdir(exist_ok=True)
    src=base.BUILD/"src/Live141Backend.kt"
    backend=src.read_text()
    backend=replace(backend,"    private var initial = emptyMap<String, String>()",'''    private val checkedKeys = setOf("arrays-exp", "arrays-eager-index", "bv-eager-eval", "simplification",
        "static-learning", "bv-to-bool", "incremental", "produce-models", "produce-unsat-cores", "tlimit-per", "seed")
    private var experimentOptions: Map<String, String>? = null
    private var methodPre = emptyMap<String, String>()
    private var methodPost = emptyMap<String, String>()

    fun select(current: Map<String, String>) {
        check(current.keys.all { it in checkedKeys })
        experimentOptions = current
        methodPre = emptyMap()
        methodPost = emptyMap()
    }

    fun observed(): Map<String, Map<String, String>> = mapOf("before" to methodPre, "after" to methodPost)

    private fun selectedOptions(solver: Solver): Map<String, String> = checkedKeys.associateWith(solver::getOption)

    private var initial = emptyMap<String, String>()''')
    backend=replace(backend,"            for ((key, value) in overrides) {",
                    "            for ((key, value) in (experimentOptions ?: overrides)) {")
    backend=replace(backend,"            return solver",'''            if (experimentOptions != null && methodPre.isEmpty()) {
                methodPre = selectedOptions(native)
                ClientStats.afterFirstCheck = { methodPost = selectedOptions(native) }
            }
            return solver''')
    (output/"Live141Backend.kt").write_text(backend)

    driver=(ROOT.parent/"followup/src/PowerDriver.kt").read_text()
    driver=replace(driver,'    if (cfg.containsKey("methods")) {',
                             '    if (cfg.containsKey("workload")) {')
    start=driver.index('        val requested = File(cfg.getValue("methods")).readLines()')
    end=driver.index('        event("batch_end")',start)
    driver=driver[:start]+'''        val requested = File(cfg.getValue("workload")).readLines()
        val results = File(directory, "results.jsonl")
        event("batch_start")
        for (line in requested) {
            val fields = line.split("\\t", limit = 5)
            check(fields.size == 5)
            val (id, tag, label, selector, optionText) = fields
            val selected = optionText.split(',').filter(String::isNotEmpty)
                .associate { it.substringBefore('=') to it.substringAfter('=') }
            Live141Backend.select(selected)
            val methodOptions = opts.copy(pathSelectionStrategies = listOf(PathSelectionStrategy.valueOf(selector)))
            event("method_start", id)
            println("STOCK_METHOD_START $id")
            val (row) = measure(cp, tag, methodOptions)
            val values = Json.parseToJsonElement(row).jsonObject
            val extended = buildJsonObject {
                for ((key, value) in values) put(key, value)
                put("id", id)
                put("option_label", label)
                put("selector", selector)
                put("native", Json.encodeToJsonElement(
                    kotlinx.serialization.serializer<Map<String, Map<String, String>>>(), Live141Backend.observed()))
            }
            results.appendText(extended.toString() + "\\n")
            event("method_end", id)
            println("STOCK_METHOD_END $id")
            check(rssMb() <= 2850) { "JVM RSS guard: ${rssMb()} MiB" }
        }
''' + driver[end:]
    (output/"WarmDriver.kt").write_text(driver)


def prepare_theories():
    output=ROOT/"build/src"
    wsrc=ROOT.parent/"followup/src/WorkTrace.kt"
    trace=wsrc.read_text()
    trace=replace(trace,"import io.ksmt.expr.KExpr", "import io.ksmt.expr.KExpr\nimport io.ksmt.utils.KExprTheoryRequirement")
    trace=replace(trace,"    private var steps = 0",'''    private var steps = 0
    private val footprints = IdentityHashMap<KExpr<*>, Set<String>>()
    private val theoryChecks = sortedMapOf<String, Int>()
    private var pendingTheory = "Bool"''')
    trace=replace(trace,"        expressions.clear()","        expressions.clear()\n        footprints.clear()\n        theoryChecks.clear()")
    old='''    fun beforeCheck(assumptions: List<KExpr<*>>) {
        if (enabled) event("check:${steps}", assumptions.map(::atom))
    }'''
    new='''    fun beforeCheck(assumptions: List<KExpr<*>>, stack: List<KExpr<*>>) {
        if (!enabled) return
        event("check:${steps}", assumptions.map(::atom))
        val used = sortedSetOf<String>()
        for (expr in stack + assumptions) {
            used.addAll(footprints.getOrPut(expr) {
                runCatching {
                    val requirement = KExprTheoryRequirement(expr.ctx)
                    requirement.apply(expr)
                    requirement.usedTheories.map { it.name }.toSet() +
                        (if (requirement.hasQuantifiers) setOf("Quantifiers") else emptySet())
                }.getOrElse { setOf("ClassificationError") }
            })
        }
        pendingTheory = used.joinToString("+").ifEmpty { "Bool" }
    }'''
    trace=replace(trace,old,new)
    trace=replace(trace,'        event("result:$status")','        event("result:$status")\n        theoryChecks.merge(pendingTheory, 1, Int::plus)')
    marker = '                "\\"trace_events\\":$operations,\\"trace_steps\\":$steps," +'
    extra = '                "\\"theory_checks\\":${kotlinx.serialization.json.Json.encodeToString<Map<String, Int>>(theoryChecks)}," +'
    trace=replace(trace,marker,marker+"\n"+extra)
    (output/"WorkTrace.kt").write_text(trace)
    client=(ROOT.parent/"followup/src/ClientStats.kt").read_text()
    client=replace(client,"WorkTrace.beforeCheck(assumptions)", "WorkTrace.beforeCheck(assumptions, stack)")
    (output/"ClientStats.kt").write_text(client)

if __name__=="__main__":
    main()
    prepare_theories()
