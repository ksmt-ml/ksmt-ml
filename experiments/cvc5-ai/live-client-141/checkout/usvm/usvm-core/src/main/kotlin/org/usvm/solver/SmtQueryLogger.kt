package org.usvm.solver

import io.ksmt.KContext
import io.ksmt.expr.KExpr
import io.ksmt.solver.KModel
import io.ksmt.solver.KSolver
import io.ksmt.solver.KSolverConfiguration
import io.ksmt.solver.KSolverStatus
import io.ksmt.solver.z3.KZ3Solver
import io.ksmt.sort.KBoolSort
import java.io.File
import java.io.FileWriter
import kotlin.time.Duration

/**
 * Experiment hook (not part of upstream USVM): dumps every SMT check of USVM as a standalone SMT-LIB 2 file.
 * Enabled by the environment variable USVM_SMT_DIR.
 * The driver sets [SmtQueryLog.tag] to the id of the analysed method.
 */
object SmtQueryLog {
    val dir: String? = System.getenv("USVM_SMT_DIR")
    private val maxPerTag: Int = System.getenv("USVM_SMT_MAX_PER_TAG")?.toIntOrNull() ?: 3000

    @Volatile
    var tag: String = "untagged"

    private val counters = HashMap<String, Int>()
    private var jsonl: FileWriter? = null

    val enabled: Boolean get() = dir != null

    @Synchronized
    fun record(tag: String, build: (Int) -> Pair<String, String>?) {
        val d = dir ?: return
        val n = counters.getOrDefault(tag, 0)
        if (n >= maxPerTag) return
        val safe = tag.replace(Regex("[^A-Za-z0-9_.$-]"), "_").take(100) + "_" + Integer.toHexString(tag.hashCode())
        val res = build(n) ?: return
        val tagDir = File(d, safe)
        tagDir.mkdirs()
        val rel = "$safe/q%06d.smt2.gz".format(n)
        java.util.zip.GZIPOutputStream(File(d, rel).outputStream()).use {
            it.write(res.first.toByteArray())
        }
        val w = jsonl ?: FileWriter(File(d, "queries.jsonl"), true).also { jsonl = it }
        w.write(res.second.dropLast(1) + ",\"file\":\"$rel\"}\n")
        w.flush()
        counters[tag] = n + 1
    }
}

private fun sha256(s: String): String =
    java.security.MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).joinToString("") { "%02x".format(it) }

@Suppress("TooManyFunctions")
class LoggingSolver(
    private val ctx: KContext,
    private val delegate: KSolver<KSolverConfiguration>,
) : KSolver<KSolverConfiguration> {
    // When the real solver is Z3 its own native solver is printed, otherwise a mirror Z3 solver is kept.
    private val shadow: KZ3Solver? = if (delegate is KZ3Solver) null else KZ3Solver(ctx)
    private val printer: KZ3Solver = delegate as? KZ3Solver ?: shadow!!
    private val stack = ArrayList<KExpr<KBoolSort>>()
    private val scopeSizes = ArrayList<Int>()
    private var lastChecked: List<KExpr<KBoolSort>> = emptyList()

    override fun configure(configurator: KSolverConfiguration.() -> Unit) = delegate.configure(configurator)

    override fun assert(expr: KExpr<KBoolSort>) {
        delegate.assert(expr)
        shadow?.assert(expr)
        stack += expr
    }

    override fun assert(exprs: List<KExpr<KBoolSort>>) {
        exprs.forEach { assert(it) }
    }

    override fun assertAndTrack(expr: KExpr<KBoolSort>) {
        delegate.assertAndTrack(expr)
        shadow?.assert(expr)
        stack += expr
    }

    override fun assertAndTrack(exprs: List<KExpr<KBoolSort>>) {
        exprs.forEach { assertAndTrack(it) }
    }

    override fun push() {
        delegate.push()
        shadow?.push()
        scopeSizes += stack.size
    }

    override fun pop(n: UInt) {
        delegate.pop(n)
        shadow?.pop(n)
        repeat(n.toInt()) {
            val size = scopeSizes.removeAt(scopeSizes.size - 1)
            while (stack.size > size) stack.removeAt(stack.size - 1)
        }
    }

    override fun check(timeout: Duration): KSolverStatus = logged(emptyList()) { delegate.check(timeout) }

    override fun checkWithAssumptions(assumptions: List<KExpr<KBoolSort>>, timeout: Duration): KSolverStatus =
        logged(assumptions) { delegate.checkWithAssumptions(assumptions, timeout) }

    private fun logged(assumptions: List<KExpr<KBoolSort>>, run: () -> KSolverStatus): KSolverStatus {
        if (!SmtQueryLog.enabled) return run()
        val t0 = System.nanoTime()
        val status = run()
        val ms = (System.nanoTime() - t0) / 1_000_000.0
        val tag = SmtQueryLog.tag
        SmtQueryLog.record(tag) { qid ->
            val text = if (assumptions.isEmpty()) {
                printer.nativeSolver().toString()
            } else {
                // The real solver must keep its current model, so print from a short-lived solver.
                KZ3Solver(ctx).use { tmp ->
                    stack.forEach { tmp.assert(it) }
                    assumptions.forEach { tmp.assert(it) }
                    tmp.nativeSolver().toString()
                }
            }
            val all = stack + assumptions
            var common = 0
            while (common < all.size && common < lastChecked.size && all[common] == lastChecked[common]) common++
            lastChecked = all
            val body = "(set-logic ALL)\n" + text + "\n(check-sat)\n"
            val json = "{\"tag\":\"${tag.replace("\\", "\\\\").replace("\"", "\\\"")}\",\"qid\":$qid," +
                "\"n_assert\":${stack.size},\"n_assume\":${assumptions.size},\"depth\":${scopeSizes.size}," +
                "\"prefix_with_prev\":$common,\"bytes\":${body.length},\"sha256\":\"${sha256(body)}\",\"usvm_status\":\"$status\"," +
                "\"usvm_ms\":$ms}"
            body to json
        }
        return status
    }

    override fun model(): KModel = delegate.model()

    override fun unsatCore(): List<KExpr<KBoolSort>> = delegate.unsatCore()

    override fun reasonOfUnknown(): String = delegate.reasonOfUnknown()

    override fun interrupt() = delegate.interrupt()

    override fun close() {
        delegate.close()
        shadow?.close()
    }
}
