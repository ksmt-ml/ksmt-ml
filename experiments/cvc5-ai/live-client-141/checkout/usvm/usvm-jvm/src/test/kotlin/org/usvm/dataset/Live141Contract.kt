package org.usvm.dataset

import io.ksmt.KContext
import io.ksmt.solver.KSolverStatus.SAT
import io.ksmt.solver.KSolverStatus.UNSAT
import org.usvm.machine.Live141Backend
import org.usvm.solver.TimedSolver
import java.io.File
import kotlin.time.Duration.Companion.milliseconds

internal fun solverContract(directory: File) {
    val cap = 1600.milliseconds
    KContext().use { ctx ->
        with(ctx) {
            val native = if (Live141Backend.arm == "z3") Live141Backend.defaultZ3(ctx) else Live141Backend.cvc5(ctx)
            var witness: io.ksmt.solver.KModel? = null
            val x = mkConst("live_x", bv32Sort)
            val fp = mkConst("live_fp", fp32Sort)
            val arr = mkConst("live_array", mkArraySort(bv32Sort, bv32Sort))
            val index = mkBv(11)
            val slot = mkArraySelect(arr, index)
            val seven = mkBv(7)
            val fortyOne = mkBv(41)
            val two = mkFp32(2.0f)
            val fact = mkEq(x, seven)
            val opposite = mkNot(fact)
            @Suppress("UNCHECKED_CAST")
            val delegate = native as io.ksmt.solver.KSolver<io.ksmt.solver.KSolverConfiguration>
            TimedSolver(delegate).use { s ->
                s.assert(fact)
                s.assert(mkEq(fp, two))
                s.assert(mkEq(slot, fortyOne))
                check(s.check(cap) == SAT)
                witness = s.model().detach()
                check(witness!!.eval(x, isComplete = true) == seven)
                check(witness!!.eval(fp, isComplete = true) == two)
                check(witness!!.eval(slot, isComplete = true) == fortyOne)
                s.push()
                s.assertAndTrack(opposite)
                check(s.check(cap) == UNSAT && opposite in s.unsatCore())
                s.pop()
                check(s.checkWithAssumptions(listOf(opposite), cap) == UNSAT)
                check(opposite in s.unsatCore())
                check(s.checkWithAssumptions(listOf(fact), cap) == SAT)
                check(s.check(cap) == SAT)
                Live141Backend.dump(directory)
            }
            check(witness!!.eval(x, isComplete = true) == seven)
            check(witness!!.eval(slot, isComplete = true) == fortyOne)
        }
    }
    println("CONTRACT_PASS scalar fp array detach after close scopes assumptions unsat core")
}
