package org.usvm.solver

import io.ksmt.KContext
import io.ksmt.expr.KApp
import io.ksmt.expr.KArrayLambdaBase
import io.ksmt.expr.KExpr
import io.ksmt.expr.KFunctionApp
import io.ksmt.expr.KQuantifier
import io.ksmt.solver.KSolver
import io.ksmt.solver.KSolverStatus
import io.ksmt.solver.cvc5.KCvc5Solver
import io.ksmt.solver.z3.KZ3Solver
import io.ksmt.sort.KArithSort
import io.ksmt.sort.KArraySortBase
import io.ksmt.sort.KBoolSort
import io.ksmt.sort.KBvSort
import io.ksmt.sort.KFpSort
import io.ksmt.sort.KUninterpretedSort
import java.io.File
import java.lang.management.ManagementFactory
import kotlin.time.Duration.Companion.milliseconds

enum class ShadowOp { ASSERT, TRACK, PUSH, POP, CHECK, ASSUME, MODEL, CORE }

/** Captures a bounded ordered prefix and evaluates it after live analysis. */
class ShadowCapture private constructor(private val method: String) {
    companion object {
        private var activeMethod: String? = null
        private val captured = ArrayList<ShadowCapture>()
        private val order = System.getenv("SMT_SHADOW_ORDER")?.split(',').orEmpty()
        private val actions = mapOf(
            "A" to emptyMap(), "B" to emptyMap(), "gauss" to mapOf("bv-gauss-elim" to "true"),
            "bitwise" to mapOf("bitwise-eq" to "true"), "no-static" to mapOf("static-learning" to "false"),
            "array-index" to mapOf("arrays-eager-index" to "false"), "z3" to emptyMap(),
        )
        private val spec = actions.values.flatMap { it.keys }.toSortedSet()
        private val clock = ManagementFactory.getThreadMXBean()
        fun create(): ShadowCapture? = activeMethod?.let { tag ->
            require(order.toSet() == actions.keys && order.size == actions.size)
            ShadowCapture(tag).also { captured.add(it) }
        }

        fun begin(method: String?) {
            captured.clear()
            activeMethod = method
        }

        fun finish() {
            for (session in captured) session.evaluate()
            captured.clear()
        }
    }

    private data class Entry(
        val kind: ShadowOp,
        val expressions: List<KExpr<KBoolSort>>,
        val depth: UInt,
        val result: KSolverStatus?,
    )

    private val operations = ArrayList<Entry>()
    private var nChecks = 0
    private var context: KContext? = null
    private var stopped = false

    fun add(kind: ShadowOp, expressions: List<KExpr<KBoolSort>> = emptyList(), depth: UInt = 0u,
            result: KSolverStatus? = null) {
        if (stopped) return
        if (operations.size == 2048) {
            stopped = true
            return
        }
        expressions.firstOrNull()?.let {
            require(context == null || context === it.ctx)
            context = it.ctx
        }
        operations += Entry(kind, expressions.toList(), depth, result)
        if (result != null && ++nChecks == 32) stopped = true
    }

    fun evaluate() {
        if (nChecks == 0) return
        val file = File("shadow.tsv")
        val context = context
        if (context != null) replayAll(context, file) else KContext().use { replayAll(it, file) }
    }

    private fun replayAll(context: KContext, file: File) {
        file.appendText("BEGIN\t$method\t${operations.size}\t$nChecks\t$stopped\n")
        file.appendText(inputShapes())
        for (arm in order) {
            try {
                val solver = if (arm == "z3") KZ3Solver(context) else KCvc5Solver(context)
                solver.use { replay(it, arm, file) }
            } catch (e: Exception) {
                val detail = e.toString().replace('\t', ' ').replace('\n', ' ').take(300)
                file.appendText("ERROR\t$arm\t$detail\n")
            }
        }
        file.appendText("END\t$method\n")
    }

    private fun inputShapes(): String = buildString {
        val stack = ArrayList<KExpr<KBoolSort>>()
        val scopes = ArrayList<Int>()
        var i = 0
        for (entry in operations) {
            when (entry.kind) {
                ShadowOp.ASSERT, ShadowOp.TRACK -> stack.addAll(entry.expressions)
                ShadowOp.PUSH -> scopes.add(stack.size)
                ShadowOp.POP -> repeat(entry.depth.toInt()) {
                    stack.subList(scopes.removeAt(scopes.lastIndex), stack.size).clear()
                }
                ShadowOp.CHECK, ShadowOp.ASSUME -> {
                    val pending = ArrayDeque<KExpr<*>>()
                    pending.addAll(stack)
                    pending.addAll(entry.expressions)
                    val seen = HashSet<KExpr<*>>()
                    val kinds = sortedSetOf<String>()
                    var products = 0
                    var remainders = 0
                    var omittedChildren = 0
                    while (pending.isNotEmpty() && seen.size < 4096) {
                        val expr = pending.removeLast()
                        if (!seen.add(expr)) continue
                        when (expr.sort) {
                            is KArithSort -> kinds.add("Arith")
                            is KArraySortBase<*> -> kinds.add("Array")
                            is KBvSort -> kinds.add("BV")
                            is KFpSort -> kinds.add("FP")
                            is KUninterpretedSort -> kinds.add("UF")
                        }
                        when (expr) {
                            is KApp<*, *> -> {
                                if (expr is KFunctionApp<*> && expr.args.isNotEmpty()) kinds.add("UF")
                                val name = expr.decl.name
                                if (name == "bvmul") products++
                                if (name in setOf("bvudiv", "bvsdiv", "bvurem", "bvsrem", "bvsmod")) remainders++
                                pending.addAll(expr.args)
                            }
                            is KQuantifier -> {
                                kinds.add("Quantifier")
                                pending.add(expr.body)
                            }
                            is KArrayLambdaBase<*, *> -> pending.add(expr.body)
                            else -> omittedChildren++
                        }
                    }
                    val theories = kinds.joinToString("+").ifEmpty { "Bool" }
                    append("SHAPE\t${++i}\t$theories\t${seen.size}\t$products\t$remainders\t${pending.isNotEmpty()}\t$omittedChildren\n")
                }
                ShadowOp.MODEL, ShadowOp.CORE -> Unit
            }
        }
    }

    private fun replay(solver: KSolver<*>, arm: String, file: File) {
        val native = (solver as? KCvc5Solver)?.nativeSolver()
        val options = actions.getValue(arm)
        options.forEach { (key, value) -> native!!.setOption(key, value) }
        fun attestation(whenRead: String): String {
            if (native == null) return "VERSION\t$arm\t${com.microsoft.z3.Version.getString()}\n"
            val settings = (spec + listOf("seed", "incremental", "produce-models", "produce-unsat-cores", "tlimit-per"))
                .joinToString(",") { key -> "$key=${native.getOption(key)}" }
            for ((key, value) in options) check(native.getOption(key) == value) { "Option reset: $key" }
            return "OPTIONS\t$arm\t$whenRead\t${native.getVersion()}\t$settings\n"
        }
        val lines = StringBuilder(attestation("before"))
        var latest: KSolverStatus? = null
        var check = 0
        var skipped = 0
        for (entry in operations) {
            when (entry.kind) {
                ShadowOp.ASSERT -> solver.assert(entry.expressions)
                ShadowOp.TRACK -> solver.assertAndTrack(entry.expressions)
                ShadowOp.PUSH -> solver.push()
                ShadowOp.POP -> solver.pop(entry.depth)
                ShadowOp.MODEL -> if (latest == KSolverStatus.SAT) solver.model().detach() else skipped++
                ShadowOp.CORE -> if (latest == KSolverStatus.UNSAT) solver.unsatCore() else skipped++
                ShadowOp.CHECK, ShadowOp.ASSUME -> {
                    val wall = System.nanoTime()
                    val cpu = clock.currentThreadCpuTime
                    val result = if (entry.kind == ShadowOp.CHECK) solver.check(250.milliseconds)
                        else solver.checkWithAssumptions(entry.expressions, 250.milliseconds)
                    val usedCpu = clock.currentThreadCpuTime - cpu
                    val usedWall = System.nanoTime() - wall
                    require(usedCpu >= 0 && usedWall >= 0)
                    val reason = if (result == KSolverStatus.UNKNOWN)
                        solver.reasonOfUnknown().replace('\t', ' ').replace('\n', ' ').take(100) else ""
                    check++
                    lines.append("CHECK\t$arm\t$check\t${entry.kind}\t${entry.result}\t$result\t$usedCpu\t$usedWall\t$reason\n")
                    if (check == 1 && native != null) lines.append(attestation("after"))
                    latest = result
                }
            }
        }
        lines.append("DONE\t$arm\t$check\t$skipped\n")
        file.appendText(lines.toString())
    }
}
