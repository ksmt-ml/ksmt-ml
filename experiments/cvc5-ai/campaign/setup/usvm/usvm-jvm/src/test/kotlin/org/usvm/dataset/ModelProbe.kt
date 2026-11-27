@file:Suppress("UNCHECKED_CAST")

package org.usvm.dataset

import io.ksmt.KContext
import io.ksmt.solver.KSolverStatus
import io.ksmt.solver.cvc5.KCvc5Solver
import io.ksmt.solver.z3.KZ3SMTLibParser
import io.ksmt.solver.z3.KZ3Solver
import java.io.File
import java.util.zip.GZIPInputStream
import kotlin.time.Duration.Companion.seconds

/**
 * Minimal harness for the cvc5 model extraction cost: args FILE.smt2.gz SOLVER(cvc5|z3) REPS [what=detach|model|decls|eval|native|none].
 * Times: parse, assert, check, model(), then the chosen extraction step, several times in one solver.
 */
fun main(args: Array<String>) {
    val file = File(args[0])
    val solverName = args[1]
    val reps = args[2].toInt()
    val what = args.getOrElse(3) { "detach" }
    val text = (if (file.name.endsWith(".gz")) GZIPInputStream(file.inputStream()) else file.inputStream()).readBytes().decodeToString()
    KContext().use { ctx ->
        var t = System.nanoTime()
        val parsed = KZ3SMTLibParser(ctx).parse(text)
        // PROBE_ITE=1 applies the table -> ite rewriting (TableIteTransformer) before asserting
        val asserts = if (System.getenv("PROBE_ITE") == "1") {
            val tr = org.usvm.solver.TableIteTransformer(ctx)
            parsed.map { tr.apply(it) }.also { println("table reads rewritten=${tr.rewritten}") }
        } else {
            parsed
        }
        println("parse ms=${(System.nanoTime() - t) / 1e6} asserts=${asserts.size}")
        if (what == "bisect") {
            // leave one assertion out at a time and time the first getValue after check-sat
            for (k in asserts.indices) {
                val sub = asserts.filterIndexed { j, _ -> j != k }
                KCvc5Solver(ctx).use { c ->
                    c.assert(sub)
                    val st0 = c.check(10.seconds)
                    val t1 = System.nanoTime()
                    if (st0 == KSolverStatus.SAT) c.model().detach()
                    println("   without assert $k: $st0 detach ms=${(System.nanoTime() - t1) / 1e6}")
                }
            }
            return
        }
        val s: io.ksmt.solver.KSolver<*> = if (solverName == "cvc5") KCvc5Solver(ctx).also { c ->
            // PROBE_OPTS="name=value,name=value" native cvc5 options applied before the first assertion
            System.getenv("PROBE_OPTS")?.split(",")?.filter { it.isNotBlank() }?.forEach { c.nativeSolver().setOption(it.substringBefore('='), it.substringAfter('=')) }
        } else KZ3Solver(ctx)
        t = System.nanoTime()
        s.assert(asserts)
        println("assert ms=${(System.nanoTime() - t) / 1e6}")
        t = System.nanoTime()
        val st = s.check(10.seconds)
        println("check $st ms=${(System.nanoTime() - t) / 1e6}")
        if (st != KSolverStatus.SAT) return
        repeat(reps) { i ->
            t = System.nanoTime()
            val m = s.model()
            val tModel = (System.nanoTime() - t) / 1e6
            t = System.nanoTime()
            var info = ""
            when (what) {
                "detach" -> m.detach()
                "decls" -> info = "decls=${m.declarations.size}"
                "eval" -> {
                    // evaluate only the constants that occur in the assertions
                    var n = 0
                    for (d in m.declarations) { if (d.argSorts.isEmpty()) { m.eval(ctx.mkApp(d, emptyList()), true); n++ } }
                    info = "evaluated=$n"
                }
                "evalasserts" -> {
                    // lazy alternative: evaluate only the terms the engine needs (here: every assertion)
                    for ((k, a) in asserts.withIndex()) {
                        val t1 = System.nanoTime()
                        val v = m.eval(a, true)
                        println("   assert $k -> $v".take(60) + " ms=${(System.nanoTime() - t1) / 1e6}")
                    }
                }
                "evalselect" -> {
                    // evaluate select(length, x) for each address constant instead of the whole array
                    val len = m.declarations.first { it.name.startsWith("length<") }
                    val lenExpr = ctx.mkApp(len, emptyList()) as io.ksmt.expr.KExpr<io.ksmt.sort.KArraySort<io.ksmt.sort.KUninterpretedSort, io.ksmt.sort.KBv32Sort>>
                    for (d in m.declarations.filter { it.sort is io.ksmt.sort.KUninterpretedSort && it.argSorts.isEmpty() }) {
                        val t1 = System.nanoTime()
                        val idx = ctx.mkApp(d, emptyList()) as io.ksmt.expr.KExpr<io.ksmt.sort.KUninterpretedSort>
                        val v = m.eval(ctx.mkArraySelect(lenExpr, idx), true)
                        println("   select(length, ${d.name}) = $v ms=${(System.nanoTime() - t1) / 1e6}")
                    }
                }
                "perdecl" -> {
                    for (d in m.declarations) {
                        val t1 = System.nanoTime()
                        m.interpretation(d)
                        println("   decl ${d.name.take(60)} : ${d.sort.toString().take(80)} ms=${(System.nanoTime() - t1) / 1e6}")
                    }
                }
                "native" -> {
                    val ns = (s as KCvc5Solver).nativeSolver()
                    val n = ns.getAssertions().size
                    info = "nativeAssertions=$n"
                }
                else -> {}
            }
            println("rep $i model() ms=$tModel step($what) ms=${(System.nanoTime() - t) / 1e6} $info")
        }
    }
}
