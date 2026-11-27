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
import java.util.Base64
import java.util.concurrent.atomic.AtomicInteger
import kotlin.time.Duration

/** Ordered KSMT transcript and the live Z3 answers, with a separate printer. */
class SessionTraceSolver(
    ctx: KContext,
    private val delegate: KSolver<KSolverConfiguration>,
) : KSolver<KSolverConfiguration> {
    companion object {
        val directory = System.getenv("USVM_SESSION_DIR")
        private val sequence = AtomicInteger()
    }

    private val printer = KZ3Solver(ctx)
    private val writer = File(directory, "%05d.events".format(sequence.getAndIncrement())).bufferedWriter()

    init {
        record("open", text = SmtQueryLog.tag)
    }

    private fun record(op: String, arg: String = "", text: String = "") {
        val encoded = Base64.getEncoder().encodeToString(text.toByteArray(Charsets.UTF_8))
        writer.append(op).append('\t').append(arg).append('\t').append(encoded).append('\n')
        writer.flush()
    }

    private fun print(expr: KExpr<KBoolSort>): String {
        printer.push()
        try {
            printer.assert(expr)
            return printer.nativeSolver().toString()
        } finally {
            printer.pop(1u)
        }
    }

    override fun configure(configurator: KSolverConfiguration.() -> Unit) {
        delegate.configure(configurator)
        record("configure")
    }

    override fun assert(expr: KExpr<KBoolSort>) {
        delegate.assert(expr)
        record("assert", text = print(expr))
    }

    override fun assert(exprs: List<KExpr<KBoolSort>>) = exprs.forEach { assert(it) }

    override fun assertAndTrack(expr: KExpr<KBoolSort>) {
        delegate.assertAndTrack(expr)
        record("assertAndTrack", text = print(expr))
    }

    override fun assertAndTrack(exprs: List<KExpr<KBoolSort>>) = exprs.forEach { assertAndTrack(it) }

    override fun push() {
        delegate.push()
        record("push")
    }

    override fun pop(n: UInt) {
        delegate.pop(n)
        record("pop", n.toString())
    }

    override fun check(timeout: Duration): KSolverStatus {
        val status = delegate.check(timeout)
        record("check", status.toString(), timeout.inWholeMilliseconds.toString())
        return status
    }

    override fun checkWithAssumptions(assumptions: List<KExpr<KBoolSort>>, timeout: Duration): KSolverStatus {
        val status = delegate.checkWithAssumptions(assumptions, timeout)
        for (assumption in assumptions) record("assumption", text = print(assumption))
        record("checkWithAssumptions", status.toString(), timeout.inWholeMilliseconds.toString())
        return status
    }

    override fun model(): KModel {
        val result = delegate.model()
        record("model")
        return result
    }

    override fun unsatCore(): List<KExpr<KBoolSort>> {
        val result = delegate.unsatCore()
        for (entry in result) record("coreTerm", text = print(entry))
        record("unsatCore")
        return result
    }

    override fun reasonOfUnknown(): String {
        val result = delegate.reasonOfUnknown()
        record("reasonOfUnknown", text = result)
        return result
    }

    override fun interrupt() {
        delegate.interrupt()
        record("interrupt")
    }

    override fun close() {
        try {
            delegate.close()
            printer.close()
            record("close")
        } finally {
            writer.close()
        }
    }
}
