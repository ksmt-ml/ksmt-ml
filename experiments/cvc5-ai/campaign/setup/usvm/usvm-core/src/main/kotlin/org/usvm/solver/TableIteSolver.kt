@file:Suppress("UNCHECKED_CAST")

package org.usvm.solver

import io.ksmt.KContext
import io.ksmt.expr.KArrayConst
import io.ksmt.expr.KArraySelect
import io.ksmt.expr.KArrayStore
import io.ksmt.expr.KExpr
import io.ksmt.expr.transformer.KNonRecursiveTransformer
import io.ksmt.solver.KModel
import io.ksmt.solver.KSolver
import io.ksmt.solver.KSolverConfiguration
import io.ksmt.solver.KSolverStatus
import io.ksmt.sort.KArraySort
import io.ksmt.sort.KBoolSort
import io.ksmt.sort.KSort
import kotlin.time.Duration

/**
 * Experiment workaround (not part of upstream USVM) for the slow cvc5 model construction on constant lookup tables.
 * A read of a store chain over a constant array, select(store(...store(const(d), i0, v0)..., in, vn), idx), is rewritten to
 * ite(idx = in, vn, ... ite(idx = i0, v0, d)), which is equivalent (the last store wins) and removes the array from the query.
 * The unsat core is mapped back to the original assumptions. Enabled by the environment variable USVM_TABLE_ITE=1.
 */
class TableIteTransformer(ctx: KContext) : KNonRecursiveTransformer(ctx) {
    var rewritten = 0L

    override fun <D : KSort, R : KSort> transform(expr: KArraySelect<D, R>): KExpr<R> =
        transformExprAfterTransformed(expr, expr.array, expr.index) { array0, index ->
            var array: KExpr<*> = array0
            val stores = ArrayList<Pair<KExpr<*>, KExpr<*>>>()
            while (array is KArrayStore<*, *>) {
                stores += array.index to array.value
                array = array.array
            }
            if (array is KArrayConst<*, *> && stores.isNotEmpty()) {
                rewritten++
                var acc: KExpr<R> = array.value as KExpr<R>
                for ((i, v) in stores.asReversed()) {
                    acc = ctx.mkIte(ctx.mkEq(index as KExpr<KSort>, i as KExpr<KSort>), v as KExpr<R>, acc)
                }
                acc
            } else {
                ctx.mkArraySelect(array0 as KExpr<KArraySort<D, R>>, index as KExpr<D>)
            }
        }
}

class TableIteSolver(private val ctx: KContext, private val delegate: KSolver<KSolverConfiguration>) : KSolver<KSolverConfiguration> {
    private val transformer = TableIteTransformer(ctx)
    private val back = HashMap<KExpr<KBoolSort>, KExpr<KBoolSort>>()

    private fun t(e: KExpr<KBoolSort>): KExpr<KBoolSort> = transformer.apply(e)

    override fun configure(configurator: KSolverConfiguration.() -> Unit) = delegate.configure(configurator)
    override fun assert(expr: KExpr<KBoolSort>) = delegate.assert(t(expr))
    override fun assert(exprs: List<KExpr<KBoolSort>>) = delegate.assert(exprs.map(::t))
    override fun assertAndTrack(expr: KExpr<KBoolSort>) = delegate.assertAndTrack(t(expr))
    override fun assertAndTrack(exprs: List<KExpr<KBoolSort>>) = delegate.assertAndTrack(exprs.map(::t))
    override fun push() = delegate.push()
    override fun pop(n: UInt) = delegate.pop(n)
    override fun check(timeout: Duration): KSolverStatus = delegate.check(timeout)
    override fun checkWithAssumptions(assumptions: List<KExpr<KBoolSort>>, timeout: Duration): KSolverStatus {
        back.clear()
        val ta = assumptions.map { a -> t(a).also { back[it] = a } }
        return delegate.checkWithAssumptions(ta, timeout)
    }

    override fun model(): KModel = delegate.model()
    override fun unsatCore(): List<KExpr<KBoolSort>> = delegate.unsatCore().map { back[it] ?: it }
    override fun reasonOfUnknown(): String = delegate.reasonOfUnknown()
    override fun interrupt() = delegate.interrupt()
    override fun close() = delegate.close()
}
