package org.usvm.dataset

import io.ksmt.KContext
import io.ksmt.expr.KExpr
import io.ksmt.solver.KSolver
import io.ksmt.solver.KSolverConfiguration
import io.ksmt.solver.KSolverStatus
import io.ksmt.solver.KSolverUnsupportedFeatureException
import io.ksmt.sort.KBoolSort
import io.ksmt.sort.KBv1Sort
import io.ksmt.sort.KBvSort
import io.ksmt.sort.KFpSort
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.usvm.machine.Live141Backend
import org.usvm.solver.TimedSolver
import java.io.File
import kotlin.time.Duration.Companion.milliseconds

private val cap = 1600.milliseconds

private data class FpCase(val label: String, val raw: Long, val nan: Boolean = false)

private val cases32 = listOf(
    FpCase("positive-zero", 0x00000000L),
    FpCase("negative-zero", 0x80000000L),
    FpCase("negative-finite", 0xc1200000L),
    FpCase("subnormal", 0x00000001L),
    FpCase("positive-infinity", 0x7f800000L),
    FpCase("negative-infinity", 0xff800000L),
    FpCase("nan", 0x7fc01234L, true),
)
private val cases64 = listOf(
    FpCase("positive-zero", 0L),
    FpCase("negative-zero", Long.MIN_VALUE),
    FpCase("negative-finite", 0xc024000000000000UL.toLong()),
    FpCase("subnormal", 1L),
    FpCase("positive-infinity", 0x7ff0000000000000L),
    FpCase("negative-infinity", 0xfff0000000000000UL.toLong()),
    FpCase("nan", 0x7ff8000000001234L, true),
)

@Suppress("UNCHECKED_CAST")
private fun KContext.fpFromRaw(b: KExpr<KBvSort>, width: Int, fraction: Int): KExpr<KFpSort> =
    mkFpFromBvExpr(mkBvExtractExpr(width - 1, width - 1, b) as KExpr<KBv1Sort>,
        mkBvExtractExpr(width - 2, fraction, b), mkBvExtractExpr(fraction - 1, 0, b))

fun main(args: Array<String>) {
    val directory = File(args.single())
    val out = File(directory, "results.jsonl")
    KContext().use { ctx ->
        with(ctx) {
            val z3 = Live141Backend.arm == "z3"
            @Suppress("UNCHECKED_CAST")
            val raw = (if (z3) Live141Backend.defaultZ3(ctx) else Live141Backend.cvc5(ctx)) as
                KSolver<KSolverConfiguration>
            TimedSolver(raw).use { solver ->
                check(solver.check(cap) == KSolverStatus.SAT)
                Live141Backend.dump(directory)
                for ((width, frac, tests) in listOf(Triple(32,23,cases32), Triple(64,52,cases64))) {
                    val fs = mkFpSort((width - frac - 1).toUInt(), (frac + 1).toUInt())
                    val bs = mkBvSort(width.toUInt())
                    for (test in tests) for (mode in listOf("direct","inverse")) {
                        if (test.nan && mode == "direct") continue
                        for (counterexample in listOf(false,true)) {
                            solver.push()
                            val id = "${mode}_${width}_${test.label}_$counterexample"
                            val first = System.nanoTime()
                            var status = ""
                            var model: Boolean? = null
                            var detail = ""
                            try {
                                val x = mkConst("x_$id", fs)
                                val bits = mkConst("bits_$id", bs)
                                val given = mkBv(test.raw, width.toUInt())
                                val nan = mkFpIsNaNExpr(x)
                                solver.assert(mkEq(x, fpFromRaw(given, width, frac)))
                                val value = if (mode == "direct") mkFpToIEEEBvExprNoSimplify(x) else {
                                    // Equality of FP terms distinguishes +0 and -0. NaN is canonicalized only for this API.
                                    solver.assert(mkOr(nan, mkEq(fpFromRaw(bits, width, frac), x)))
                                    val canonical = if (width == 32) 0x7fc00000L else 0x7ff8000000000000L
                                    mkIte(nan, mkBv(canonical, width.toUInt()), bits)
                                }
                                val expected = if (test.nan) {
                                    mkBv(if (width == 32) 0x7fc00000L else 0x7ff8000000000000L, width.toUInt())
                                } else given
                                val assertion: KExpr<KBoolSort> = mkEq(value, expected)
                                solver.assert(if (counterexample) mkNot(assertion) else assertion)
                                val outcome = solver.check(cap)
                                status = outcome.name
                                if (outcome == KSolverStatus.SAT) {
                                    model = solver.model().eval(value, isComplete = true) == expected
                                }
                                if (outcome == KSolverStatus.UNKNOWN) detail = solver.reasonOfUnknown()
                            } catch (unsupported: KSolverUnsupportedFeatureException) {
                                status = "unsupported"
                                detail = unsupported.message.orEmpty()
                            } catch (failure: Exception) {
                                status = "error"
                                detail = failure.toString()
                            } finally {
                                solver.pop()
                            }
                            val stop = System.nanoTime()
                            out.appendText(buildJsonObject {
                                put("case", test.label)
                                put("width", width)
                                put("mode", mode)
                                put("expected", if (counterexample) "UNSAT" else "SAT")
                                put("status", status)
                                put("model_correct", model)
                                put("detail", detail)
                                put("wall_ms", (stop - first) / 1e6)
                            }.toString() + "\n")
                        }
                    }
                }
            }
        }
    }
}
