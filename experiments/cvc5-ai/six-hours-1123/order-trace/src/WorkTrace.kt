package org.usvm.solver

import io.ksmt.expr.KExpr
import org.jacodb.api.jvm.cfg.JcInst
import org.usvm.PathNode
import java.security.MessageDigest
import java.util.Arrays
import java.util.HexFormat
import java.util.IdentityHashMap

/** Identifies ordered observable USVM input and operation outcomes without retaining term text. */
object WorkTrace {
    val enabled = System.getenv("SMT_TRACE") == "1"
    val sortInput = System.getenv("SMT_SORT_INPUT") == "1"
    private var sortedLists = 0L
    private var reorderedLists = 0L
    private var sortNs = 0L
    private var unorderedQueryChain = ByteArray(32)
    private val unorderedPrefixes = ArrayList<String>()
    private val compareAtoms = Comparator<ByteArray> { a, b -> Arrays.compareUnsigned(a, b) }
    val delayNs = (System.getenv("SMT_DELAY_US")?.toLong() ?: 0L) * 1000L
    var injectedNs: Long = 0
        private set
    private val digest = MessageDigest.getInstance("SHA-256")
    private val hex = HexFormat.of()
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
    }
    private val expressions = IdentityHashMap<KExpr<*>, ByteArray>()
    private val paths = IdentityHashMap<PathNode<JcInst>, ByteArray>()
    private val prefixes = ArrayList<String>()
    private val zero = ByteArray(32)
    private var chain = zero
    private var operations = 0
    private var steps = 0

    fun reset() {
        injectedNs = 0
        expressions.clear()
        paths.clear()
        prefixes.clear()
        pathEvents.clear()
        queryInputs.clear()
        unorderedPrefixes.clear()
        unorderedQueryChain = zero
        sortedLists = 0
        reorderedLists = 0
        sortNs = 0
        pathChain = zero
        queryChain = zero
        opChain = zero
        chain = zero
        operations = 0
        steps = 0
    }

    private fun text(value: String) {
        val bytes = value.toByteArray(Charsets.UTF_8)
        repeat(4) { digest.update((bytes.size ushr (8 * it)).toByte()) }
        digest.update(bytes)
    }

    private fun atom(e: KExpr<*>): ByteArray = expressions.getOrPut(e) {
        text(e.javaClass.name)
        text(e.sort.toString())
        text(e.toString())
        digest.digest()
    }

    fun <E : KExpr<*>> ordered(xs: List<E>): List<E> {
        if (!sortInput || xs.size < 2) return xs
        val from = System.nanoTime()
        val result = xs.sortedWith { a, b -> compareAtoms.compare(atom(a), atom(b)) }
        sortedLists++
        if (result != xs) reorderedLists++
        sortNs += System.nanoTime() - from
        return result
    }

    private fun event(label: String, value: List<ByteArray> = emptyList()) {
        digest.update(chain)
        text(label)
        value.forEach { digest.update(it) }
        chain = digest.digest()
        operations++
    }

    fun solver(event: String, exprs: List<KExpr<*>> = emptyList()) {
        if (!enabled) return
        val atoms = exprs.map(::atom)
        event(event, atoms)
        opChain = extension(opChain, event, atoms)
    }

    fun state(path: PathNode<JcInst>) {
        if (!enabled) return
        val pending = ArrayDeque<PathNode<JcInst>>()
        var at = path
        while (at.parent != null && !paths.containsKey(at)) {
            pending.addFirst(at)
            at = requireNotNull(at.parent)
        }
        var prior = paths[at] ?: zero
        for (p in pending) {
            val loc = p.statement.location
            val method = loc.method
            digest.update(prior)
            text(method.enclosingClass.name)
            text(method.name)
            text(method.description)
            text(loc.index.toString())
            prior = digest.digest()
            paths[p] = prior
        }
        event("step:${path.depth}", listOf(prior))
        pathChain = extension(pathChain, "step:${path.depth}", listOf(prior))
        if (pathEvents.size < 512) pathEvents.add(hex.formatHex(pathChain))
        steps++
    }

    fun beforeCheck(assumptions: List<KExpr<*>>, stack: List<KExpr<*>>) {
        if (!enabled) return
        val args = assumptions.map(::atom)
        event("check:${steps}", args)
        val assertions = stack.map(::atom)
        queryChain = extension(queryChain, "assertions:${assertions.size}", assertions)
        queryChain = extension(queryChain, "assumptions:${args.size}", args)
        if (queryInputs.size < 512) queryInputs.add(hex.formatHex(queryChain))
        unorderedQueryChain = extension(unorderedQueryChain, "assertions:${assertions.size}", assertions.sortedWith(compareAtoms))
        unorderedQueryChain = extension(unorderedQueryChain, "assumptions:${args.size}", args.sortedWith(compareAtoms))
        if (unorderedPrefixes.size < 512) unorderedPrefixes.add(hex.formatHex(unorderedQueryChain))
        opChain = extension(opChain, "check:${args.size}", args)
    }

    fun afterCheck(status: String) {
        if (!enabled) return
        event("result:$status")
        opChain = extension(opChain, "result:$status", emptyList())
        if (prefixes.size < 512) prefixes.add(hex.formatHex(chain))
    }

    fun inject() {
        if (delayNs == 0L) return
        val start = System.nanoTime()
        var duration: Long
        do {
            Thread.onSpinWait()
            duration = System.nanoTime() - start
        } while (duration < delayNs)
        injectedNs += duration
    }

    fun json(): String =
        ",\"injected_ms\":${injectedNs / 1e6},\"trace_enabled\":$enabled" +
            if (enabled) ",\"work_sha256\":\"${hex.formatHex(chain)}\"," +
                "\"trace_events\":$operations,\"trace_steps\":$steps," +
                "\"path_sha256\":\"${hex.formatHex(pathChain)}\"," +
                "\"query_sha256\":\"${hex.formatHex(queryChain)}\"," +
                "\"unordered_query_sha256\":\"${hex.formatHex(unorderedQueryChain)}\"," +
                "\"unordered_query_prefix_sha256\":${unorderedPrefixes.joinToString(prefix = "[", postfix = "]") { "\"$it\"" }}," +
                "\"sort_input\":$sortInput,\"sorted_lists\":$sortedLists,\"reordered_lists\":$reorderedLists,\"sort_ms\":${sortNs / 1e6}," +
                "\"operations_sha256\":\"${hex.formatHex(opChain)}\"," +
                "\"path_prefix_sha256\":${pathEvents.joinToString(prefix = "[", postfix = "]") { "\"$it\"" }}," +
                "\"query_prefix_sha256\":${queryInputs.joinToString(prefix = "[", postfix = "]") { "\"$it\"" }}," +
                "\"check_prefix_sha256\":${prefixes.joinToString(prefix = "[", postfix = "]") { "\"$it\"" }}"
            else ""
}
