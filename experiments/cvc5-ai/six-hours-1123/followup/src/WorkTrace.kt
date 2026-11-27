package org.usvm.solver

import io.ksmt.expr.KExpr
import org.jacodb.api.jvm.cfg.JcInst
import org.usvm.PathNode
import java.security.MessageDigest
import java.util.HexFormat
import java.util.IdentityHashMap

/** Identifies ordered observable USVM input and operation outcomes without retaining term text. */
object WorkTrace {
    val enabled = System.getenv("SMT_TRACE") == "1"
    val delayNs = (System.getenv("SMT_DELAY_US")?.toLong() ?: 0L) * 1000L
    var injectedNs: Long = 0
        private set
    private val digest = MessageDigest.getInstance("SHA-256")
    private val hex = HexFormat.of()
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

    private fun event(label: String, value: List<ByteArray> = emptyList()) {
        digest.update(chain)
        text(label)
        value.forEach { digest.update(it) }
        chain = digest.digest()
        operations++
    }

    fun solver(event: String, exprs: List<KExpr<*>> = emptyList()) {
        if (enabled) event(event, exprs.map(::atom))
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
        steps++
    }

    fun beforeCheck(assumptions: List<KExpr<*>>) {
        if (enabled) event("check:${steps}", assumptions.map(::atom))
    }

    fun afterCheck(status: String) {
        if (!enabled) return
        event("result:$status")
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
                "\"check_prefix_sha256\":${prefixes.joinToString(prefix = "[", postfix = "]") { "\"$it\"" }}"
            else ""
}
