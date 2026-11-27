"""Build independent path, input and operation chains alongside the original combined hash."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def one(text,old,new):
    assert text.count(old)==1,(old,text.count(old))
    return text.replace(old,new)

def prep():
    src=ROOT.parent/"followup/src"
    text=(src/"WorkTrace.kt").read_text()
    text=one(text,'    private val hex = HexFormat.of()', '''    private val hex = HexFormat.of()
    private val componentHash = MessageDigest.getInstance("SHA-256")
    private val pathEvents = ArrayList<String>()
    private val queryInputs = ArrayList<String>()
    private var pathChain = ByteArray(32)
    private var queryChain = ByteArray(32)
    private var opChain = ByteArray(32)

    private fun extension(prior: ByteArray, title: String, values: List<ByteArray>): ByteArray {
        componentHash.update(prior)
        val label = title.toByteArray(Charsets.UTF_8)
        componentHash.update(label.size.toByte())
        componentHash.update(label)
        values.forEach { componentHash.update(it) }
        return componentHash.digest()
    }''')
    text=one(text,'        prefixes.clear()', '''        prefixes.clear()
        pathEvents.clear()
        queryInputs.clear()
        pathChain = zero
        queryChain = zero
        opChain = zero''')
    text=one(text,'        if (enabled) event(event, exprs.map(::atom))', '''        if (!enabled) return
        val atoms = exprs.map(::atom)
        event(event, atoms)
        opChain = extension(opChain, event, atoms)''')
    text=one(text,'        event("step:${path.depth}", listOf(prior))', '''        event("step:${path.depth}", listOf(prior))
        pathChain = extension(pathChain, "step:${path.depth}", listOf(prior))
        if (pathEvents.size < 512) pathEvents.add(hex.formatHex(pathChain))''')
    text=one(text,'''    fun beforeCheck(assumptions: List<KExpr<*>>) {
        if (enabled) event("check:${steps}", assumptions.map(::atom))
    }''', '''    fun beforeCheck(assumptions: List<KExpr<*>>, stack: List<KExpr<*>>) {
        if (!enabled) return
        val args = assumptions.map(::atom)
        event("check:${steps}", args)
        val assertions = stack.map(::atom)
        queryChain = extension(queryChain, "assertions:${assertions.size}", assertions)
        queryChain = extension(queryChain, "assumptions:${args.size}", args)
        if (queryInputs.size < 512) queryInputs.add(hex.formatHex(queryChain))
        opChain = extension(opChain, "check:${args.size}", args)
    }''')
    text=one(text,'        event("result:$status")', '''        event("result:$status")
        opChain = extension(opChain, "result:$status", emptyList())''')
    old='''                "\\"trace_events\\":$operations,\\"trace_steps\\":$steps," +'''
    new=old+'''
                "\\"path_sha256\\":\\"${hex.formatHex(pathChain)}\\"," +
                "\\"query_sha256\\":\\"${hex.formatHex(queryChain)}\\"," +
                "\\"operations_sha256\\":\\"${hex.formatHex(opChain)}\\"," +
                "\\"path_prefix_sha256\\":${pathEvents.joinToString(prefix = "[", postfix = "]") { "\\"$it\\"" }}," +
                "\\"query_prefix_sha256\\":${queryInputs.joinToString(prefix = "[", postfix = "]") { "\\"$it\\"" }}," +'''
    text=one(text,old,new)
    (ROOT/"src/WorkTrace.kt").write_text(text)
    timed=(src/"ClientStats.kt").read_text()
    timed=one(timed,"WorkTrace.beforeCheck(assumptions)","WorkTrace.beforeCheck(assumptions, stack)")
    (ROOT/"src/ClientStats.kt").write_text(timed)

if __name__=="__main__":
    prep()
