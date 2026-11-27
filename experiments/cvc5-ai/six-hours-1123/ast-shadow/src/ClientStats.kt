package org.usvm.solver

import io.ksmt.expr.KExpr
import io.ksmt.solver.KModel
import io.ksmt.solver.KSolver
import io.ksmt.solver.KSolverConfiguration
import io.ksmt.solver.KSolverStatus
import io.ksmt.sort.KBoolSort
import kotlin.time.Duration

/**
 * Experiment hook (not part of upstream USVM): accumulates how USVM uses the SMT solver and where the time goes.
 * Enabled by the environment variable USVM_CLIENT_STATS=1. The driver calls [snapshotAndReset] after each method.
 */
object ClientStats {
    val enabled: Boolean = System.getenv("USVM_CLIENT_STATS") == "1"

    // all times in nanoseconds
    @JvmField var internalCheckNs = 0L
    @JvmField var internalChecks = 0L
    @JvmField var pcFalseShortcuts = 0L
    @JvmField var translateNs = 0L
    @JvmField var assertNs = 0L
    @JvmField var assertCalls = 0L
    @JvmField var assertedExprs = 0L
    @JvmField var pushNs = 0L
    @JvmField var popNs = 0L
    @JvmField var pushes = 0L
    @JvmField var pops = 0L
    @JvmField var checkNs = 0L
    @JvmField var checks = 0L
    @JvmField var checksWithAssumptions = 0L
    @JvmField var assumptionExprs = 0L
    @JvmField var sat = 0L
    @JvmField var unsat = 0L
    @JvmField var unknown = 0L
    @JvmField var satNs = 0L
    @JvmField var unsatNs = 0L
    @JvmField var unknownNs = 0L
    @JvmField var maxCheckNs = 0L
    @JvmField var modelNs = 0L
    @JvmField var modelCalls = 0L
    @JvmField var unsatCoreNs = 0L
    @JvmField var unsatCoreCalls = 0L
    @JvmField var decodeNs = 0L
    @JvmField var typeSolverNs = 0L
    @JvmField var typeUnsatRounds = 0L
    @JvmField var createSolverNs = 0L
    @JvmField var dupOrderChecks = 0L
    @JvmField var dupOrderNs = 0L
    @JvmField var dupSetChecks = 0L
    @JvmField var dupSetNs = 0L
    @JvmField var dupSatNs = 0L
    @JvmField var dupSatChecks = 0L
    @JvmField var steps = 0L
    @JvmField var forkingSteps = 0L
    @JvmField var terminated = 0L
    @JvmField var coveragePct = 0.0
    var completedPaths = 0L
    var returnedPaths = 0L
    var stopReason = "queue_exhausted"
    val branchEdges = sortedSetOf<String>()
    val exceptionTypes = sortedMapOf<String, Int>()
    val unknownReasons = sortedMapOf<String, Int>()
    var afterFirstCheck: (() -> Unit)? = null
    @JvmField var assertTotalAtChecks = 0L
    @JvmField var sumCheckNsOver100ms = 0L
    @JvmField var checksOver100ms = 0L
    @JvmField var checkMsList = ArrayList<Double>()
    @JvmField var assertMsPerCheckList = ArrayList<Double>()

    @Synchronized
    fun snapshotAndReset(): String {
        val s = "{" +
            "\"internal_checks\":$internalChecks,\"pc_false_shortcuts\":$pcFalseShortcuts,\"internal_check_ms\":${internalCheckNs / 1e6}," +
            "\"translate_ms\":${translateNs / 1e6},\"assert_ms\":${assertNs / 1e6},\"assert_calls\":$assertCalls,\"asserted_exprs\":$assertedExprs," +
            "\"push_ms\":${pushNs / 1e6},\"pop_ms\":${popNs / 1e6},\"pushes\":$pushes,\"pops\":$pops," +
            "\"check_ms\":${checkNs / 1e6},\"checks\":$checks,\"checks_assume\":$checksWithAssumptions,\"assume_exprs\":$assumptionExprs," +
            "\"sat\":$sat,\"unsat\":$unsat,\"unknown\":$unknown,\"sat_ms\":${satNs / 1e6},\"unsat_ms\":${unsatNs / 1e6},\"unknown_ms\":${unknownNs / 1e6}," +
            "\"max_check_ms\":${maxCheckNs / 1e6},\"model_ms\":${modelNs / 1e6},\"model_calls\":$modelCalls," +
            "\"unsat_core_ms\":${unsatCoreNs / 1e6},\"unsat_core_calls\":$unsatCoreCalls," +
            "\"decode_ms\":${decodeNs / 1e6},\"type_solver_ms\":${typeSolverNs / 1e6},\"type_unsat_rounds\":$typeUnsatRounds," +
            "\"create_solver_ms\":${createSolverNs / 1e6}," +
            "\"dup_order_checks\":$dupOrderChecks,\"dup_order_ms\":${dupOrderNs / 1e6}," +
            "\"dup_set_checks\":$dupSetChecks,\"dup_set_ms\":${dupSetNs / 1e6}," +
            "\"dup_sat_checks\":$dupSatChecks,\"dup_sat_ms\":${dupSatNs / 1e6}," +
            "\"steps\":$steps,\"forking_steps\":$forkingSteps,\"terminated\":$terminated,\"coverage_pct\":$coveragePct," +
            "\"assert_total_at_checks\":$assertTotalAtChecks,\"checks_over_100ms\":$checksOver100ms,\"checks_over_100ms_ms\":${sumCheckNsOver100ms / 1e6}," +
            "\"check_ms_list\":[${checkMsList.joinToString(",") { "%.3f".format(java.util.Locale.ROOT, it) }}]," +
            "\"assert_ms_per_check_list\":[${assertMsPerCheckList.joinToString(",") { "%.3f".format(java.util.Locale.ROOT, it) }}]}"
        val json = kotlinx.serialization.json.Json
        val extra = ",\"stop_reason\":\"${stopReason}\",\"branch_edges\":${json.encodeToString(branchEdges.toList())}" +
            ",\"completed_paths\":$completedPaths,\"returned_paths\":$returnedPaths" +
            ",\"exception_types\":${json.encodeToString<Map<String, Int>>(exceptionTypes)}" +
            ",\"unknown_reasons\":${json.encodeToString<Map<String, Int>>(unknownReasons)}"
        val trace = WorkTrace.json()
        reset()
        return s.dropLast(1) + extra + trace + "}"
    }

    @Synchronized
    fun reset() {
        WorkTrace.reset()
        internalCheckNs = 0; internalChecks = 0; pcFalseShortcuts = 0; translateNs = 0
        assertNs = 0; assertCalls = 0; assertedExprs = 0; pushNs = 0; popNs = 0; pushes = 0; pops = 0
        checkNs = 0; checks = 0; checksWithAssumptions = 0; assumptionExprs = 0
        sat = 0; unsat = 0; unknown = 0; satNs = 0; unsatNs = 0; unknownNs = 0; maxCheckNs = 0
        modelNs = 0; modelCalls = 0; unsatCoreNs = 0; unsatCoreCalls = 0
        decodeNs = 0; typeSolverNs = 0; typeUnsatRounds = 0; createSolverNs = 0
        dupOrderChecks = 0; dupOrderNs = 0; dupSetChecks = 0; dupSetNs = 0; dupSatNs = 0; dupSatChecks = 0
        steps = 0; forkingSteps = 0; terminated = 0; coveragePct = 0.0
        assertTotalAtChecks = 0; sumCheckNsOver100ms = 0; checksOver100ms = 0
        checkMsList = ArrayList(); assertMsPerCheckList = ArrayList()
        completedPaths = 0L; returnedPaths = 0L; stopReason = "queue_exhausted"
        branchEdges.clear(); exceptionTypes.clear(); unknownReasons.clear()
    }

    inline fun <T> timed(add: (Long) -> Unit, block: () -> T): T {
        val t0 = System.nanoTime()
        try {
            return block()
        } finally {
            add(System.nanoTime() - t0)
        }
    }
}

/** Times every call to the underlying KSMT solver and detects exact duplicate checks (same assertion stack). */
@Suppress("TooManyFunctions")
class TimedSolver(private val delegate: KSolver<KSolverConfiguration>) : KSolver<KSolverConfiguration> {
    private val shadow = ShadowCapture.create()
    private val stack = ArrayList<KExpr<KBoolSort>>()
    private val scopeSizes = ArrayList<Int>()
    private val seenOrder = HashSet<List<KExpr<KBoolSort>>>()
    private val seenSet = HashSet<Set<KExpr<KBoolSort>>>()
    private val satKeys = HashSet<List<KExpr<KBoolSort>>>()
    private var assertNsSinceCheck = 0L

    override fun configure(configurator: KSolverConfiguration.() -> Unit) = delegate.configure(configurator)

    override fun assert(expr: KExpr<KBoolSort>) {
        val t0 = System.nanoTime()
        if (WorkTrace.enabled) WorkTrace.solver("assert", listOf(expr))
        delegate.assert(expr)
        shadow?.add(ShadowOp.ASSERT, listOf(expr))
        val d = System.nanoTime() - t0
        ClientStats.assertNs += d; assertNsSinceCheck += d
        ClientStats.assertCalls++; ClientStats.assertedExprs++
        stack += expr
    }

    override fun assert(exprs: List<KExpr<KBoolSort>>) {
        val t0 = System.nanoTime()
        if (WorkTrace.enabled) WorkTrace.solver("assert-list", exprs)
        delegate.assert(exprs)
        shadow?.add(ShadowOp.ASSERT, exprs)
        val d = System.nanoTime() - t0
        ClientStats.assertNs += d; assertNsSinceCheck += d
        ClientStats.assertCalls++; ClientStats.assertedExprs += exprs.size
        stack += exprs
    }

    override fun assertAndTrack(expr: KExpr<KBoolSort>) {
        val t0 = System.nanoTime()
        if (WorkTrace.enabled) WorkTrace.solver("track", listOf(expr))
        delegate.assertAndTrack(expr)
        shadow?.add(ShadowOp.TRACK, listOf(expr))
        val d = System.nanoTime() - t0
        ClientStats.assertNs += d; assertNsSinceCheck += d
        ClientStats.assertCalls++; ClientStats.assertedExprs++
        stack += expr
    }

    override fun assertAndTrack(exprs: List<KExpr<KBoolSort>>) {
        val t0 = System.nanoTime()
        if (WorkTrace.enabled) WorkTrace.solver("track-list", exprs)
        delegate.assertAndTrack(exprs)
        shadow?.add(ShadowOp.TRACK, exprs)
        val d = System.nanoTime() - t0
        ClientStats.assertNs += d; assertNsSinceCheck += d
        ClientStats.assertCalls++; ClientStats.assertedExprs += exprs.size
        stack += exprs
    }

    override fun push() {
        val t0 = System.nanoTime()
        WorkTrace.solver("push")
        delegate.push()
        shadow?.add(ShadowOp.PUSH)
        ClientStats.pushNs += System.nanoTime() - t0
        ClientStats.pushes++
        scopeSizes += stack.size
    }

    override fun pop(n: UInt) {
        val t0 = System.nanoTime()
        if (WorkTrace.enabled) WorkTrace.solver("pop:$n")
        delegate.pop(n)
        shadow?.add(ShadowOp.POP, depth = n)
        ClientStats.popNs += System.nanoTime() - t0
        ClientStats.pops += n.toLong()
        repeat(n.toInt()) {
            val size = scopeSizes.removeAt(scopeSizes.size - 1)
            while (stack.size > size) stack.removeAt(stack.size - 1)
        }
    }

    override fun check(timeout: Duration): KSolverStatus =
        timedCheck(ShadowOp.CHECK, emptyList()) { delegate.check(timeout) }

    override fun checkWithAssumptions(assumptions: List<KExpr<KBoolSort>>, timeout: Duration): KSolverStatus {
        ClientStats.checksWithAssumptions++
        ClientStats.assumptionExprs += assumptions.size
        return timedCheck(ShadowOp.ASSUME, assumptions) { delegate.checkWithAssumptions(assumptions, timeout) }
    }

    private fun timedCheck(op: ShadowOp, assumptions: List<KExpr<KBoolSort>>, run: () -> KSolverStatus): KSolverStatus {
        val all: List<KExpr<KBoolSort>> = if (assumptions.isEmpty()) ArrayList(stack) else stack + assumptions
        val dupOrder = !seenOrder.add(all)
        val dupSet = !seenSet.add(all.toHashSet())
        WorkTrace.beforeCheck(assumptions)
        val t0 = System.nanoTime()
        WorkTrace.inject()
        val status = run()
        val d = System.nanoTime() - t0
        WorkTrace.afterCheck(status.name)
        shadow?.add(op, assumptions, result = status)
        ClientStats.afterFirstCheck?.let {
            ClientStats.afterFirstCheck = null
            it()
        }
        ClientStats.checks++
        ClientStats.checkNs += d
        ClientStats.assertTotalAtChecks += all.size
        if (d > ClientStats.maxCheckNs) ClientStats.maxCheckNs = d
        if (d > 100_000_000L) { ClientStats.checksOver100ms++; ClientStats.sumCheckNsOver100ms += d }
        ClientStats.checkMsList.add(d / 1e6)
        ClientStats.assertMsPerCheckList.add(assertNsSinceCheck / 1e6)
        assertNsSinceCheck = 0
        when (status) {
            KSolverStatus.SAT -> { ClientStats.sat++; ClientStats.satNs += d }
            KSolverStatus.UNSAT -> { ClientStats.unsat++; ClientStats.unsatNs += d }
            KSolverStatus.UNKNOWN -> {
                ClientStats.unknown++
                ClientStats.unknownNs += d
                ClientStats.unknownReasons.merge(delegate.reasonOfUnknown(), 1, Int::plus)
            }
        }
        if (dupOrder) { ClientStats.dupOrderChecks++; ClientStats.dupOrderNs += d }
        if (dupSet) { ClientStats.dupSetChecks++; ClientStats.dupSetNs += d }
        if (dupSet && status != KSolverStatus.UNKNOWN) { ClientStats.dupSatChecks++; ClientStats.dupSatNs += d }
        return status
    }

    override fun model(): KModel {
        // model extraction time (including detach) is measured in USolverBase
        WorkTrace.solver("model")
        ClientStats.modelCalls++
        return delegate.model().also { shadow?.add(ShadowOp.MODEL) }
    }

    override fun unsatCore(): List<KExpr<KBoolSort>> {
        val t0 = System.nanoTime()
        val c = delegate.unsatCore()
        WorkTrace.solver("core-result", c)
        shadow?.add(ShadowOp.CORE)
        ClientStats.unsatCoreNs += System.nanoTime() - t0
        ClientStats.unsatCoreCalls++
        return c
    }

    override fun reasonOfUnknown(): String = delegate.reasonOfUnknown()

    override fun interrupt() = delegate.interrupt()

    override fun close() {
        WorkTrace.solver("close")
        delegate.close()
    }
}
