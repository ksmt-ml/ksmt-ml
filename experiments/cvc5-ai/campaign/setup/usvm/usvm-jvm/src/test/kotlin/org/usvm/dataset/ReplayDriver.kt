package org.usvm.dataset

import io.ksmt.KContext
import io.ksmt.expr.KExpr
import io.ksmt.solver.KSolver
import io.ksmt.solver.KSolverConfiguration
import io.ksmt.solver.KSolverStatus
import io.ksmt.solver.cvc5.KCvc5Solver
import io.ksmt.solver.z3.KZ3SMTLibParser
import io.ksmt.solver.z3.KZ3Solver
import io.ksmt.sort.KBoolSort
import java.io.File
import java.util.zip.GZIPInputStream
import kotlin.time.Duration.Companion.milliseconds

/**
 * Experiment driver (not part of upstream USVM): replays the recorded solver checks of each method in order against a KSMT solver.
 * Arguments are key=value: queries=DIR (unpacked dataset directory of the project, contains queries.jsonl and the method dirs)
 * methods=FILE (tags, one per line) out=FILE.jsonl solver=cvc5|z3 timeoutMs=N warmup=N modes=fresh,usvm,incr
 *   fresh: new solver for every check
 *   usvm:  one solver per method, push, assert everything, check, pop (what USVM does today)
 *   incr:  one solver per method, one scope per assertion, pop to the common prefix with the previous check and assert only the new suffix
 * Every successful check is followed by model().detach() as in USVM.
 */
fun main(args: Array<String>) {
    val a = args.associate { it.substringBefore('=') to it.substringAfter('=') }
    val dir = File(a.getValue("queries"))
    val tags = File(a.getValue("methods")).readLines().filter { it.isNotBlank() }.toSet()
    val out = File(a.getValue("out"))
    val solverName = a["solver"] ?: "cvc5"
    val timeoutMs = (a["timeoutMs"] ?: "3000").toLong()
    val warmup = (a["warmup"] ?: "3").toInt()
    val modes = (a["modes"] ?: "fresh,usvm,incr").split(",")
    // finished (method, query) pairs: the run is resumable at query level
    val done = HashMap<String, MutableSet<Int>>()
    if (out.exists()) {
        out.forEachLine { l ->
            done.getOrPut(l.substringAfter("\"tag\":\"").substringBefore("\",\"qid\"")) { HashSet() }
                .add(l.substringAfter("\"qid\":").substringBefore(",").toInt())
        }
    }

    val byTag = LinkedHashMap<String, MutableList<Pair<Int, String>>>()
    dir.resolve("queries.jsonl").forEachLine { line ->
        val tag = line.substringAfter("\"tag\": \"").substringBefore("\", \"qid\"")
        val unesc = tag.replace("\\\"", "\"").replace("\\\\", "\\")
        if (unesc in tags) {
            val qid = line.substringAfter("\"qid\": ").substringBefore(",").toInt()
            val file = line.substringAfter("\"file\": \"").substringBefore("\"")
            byTag.getOrPut(unesc) { ArrayList() }.add(qid to file)
        }
    }
    val order = tags.filter { it in byTag }
    println("[replay] methods=${order.size} solver=$solverName modes=$modes")
    val rssLimitMb = (a["rssLimitMb"] ?: "2200").toLong()
    var warmed = warmup == 0
    for (tag in order) {
        val esc = tag.replace("\\", "\\\\").replace("\"", "\\\"")
        val skip = done[esc] ?: emptySet<Int>()
        val queries = byTag.getValue(tag).sortedBy { it.first }.filter { it.first !in skip }
        if (queries.isEmpty()) continue
        if (!warmed) {
            // JIT and native library warm-up on the first queries of the first method, results are discarded
            replayMethod(dir, queries.take(warmup), solverName, timeoutMs, modes) { }
            warmed = true
        }
        replayMethod(dir, queries, solverName, timeoutMs, modes) { r ->
            out.appendText("{\"tag\":\"$esc\"," + r + "}\n")
            if (rssMb() > rssLimitMb) {
                println("[replay] RSS limit reached, restart requested")
                System.exit(3)
            }
        }
        System.gc()
    }
    println("[replay] DONE")
    System.exit(0)
}

private fun rssMb(): Long =
    File("/proc/self/status").readLines().first { it.startsWith("VmRSS:") }.split(Regex("\\s+"))[1].toLong() / 1024

@Suppress("UNCHECKED_CAST")
private fun mk(name: String, ctx: KContext): KSolver<KSolverConfiguration> = when (name) {
    "cvc5" -> KCvc5Solver(ctx).also { s ->
        // REPLAY_LOGIC (e.g. QF_AUFBVFPLIA) makes cvc5 use a concrete logic instead of ALL; REPLAY_CVC5_OPTS="name=value,name=value" sets native options
        System.getenv("REPLAY_LOGIC")?.takeIf { it.isNotBlank() }?.let { s.nativeSolver().setLogic(it) }
        System.getenv("REPLAY_CVC5_OPTS")?.split(",")?.filter { it.isNotBlank() }?.forEach {
            s.nativeSolver().setOption(it.substringBefore('='), it.substringAfter('='))
        }
    } as KSolver<KSolverConfiguration>
    else -> KZ3Solver(ctx) as KSolver<KSolverConfiguration>
}

private class Timing {
    var create = 0L
    var assertN = 0L
    var check = 0L
    var model = 0L
    var pushPop = 0L
    var status = "X"
    private val tmx = java.lang.management.ManagementFactory.getThreadMXBean()
    private val cpu0 = tmx.currentThreadCpuTime
    fun total() = create + assertN + check + model + pushPop
    /** [wall total, create, assert, check, model, push/pop, thread CPU time of the whole block], microseconds. */
    fun json(prefix: String) =
        "\"${prefix}\":[${total() / 1000},${create / 1000},${assertN / 1000},${check / 1000},${model / 1000},${pushPop / 1000},${(tmx.currentThreadCpuTime - cpu0) / 1000}],\"${prefix}s\":\"$status\""
}

private fun checkAndModel(s: KSolver<KSolverConfiguration>, t: Timing, timeoutMs: Long) {
    val c0 = System.nanoTime()
    val st = s.check(timeoutMs.milliseconds)
    t.check += System.nanoTime() - c0
    t.status = when (st) { KSolverStatus.SAT -> "S"; KSolverStatus.UNSAT -> "U"; else -> "?" }
    if (st == KSolverStatus.SAT) {
        val m0 = System.nanoTime()
        s.model().detach()
        t.model += System.nanoTime() - m0
    }
}

private fun replayMethod(
    dir: File,
    queries: List<Pair<Int, String>>,
    solverName: String,
    timeoutMs: Long,
    modes: List<String>,
    emit: (String) -> Unit,
) {
    KContext().use { ctx ->
        val parser = KZ3SMTLibParser(ctx)
        val parsed = queries.map { (qid, file) ->
            val text = GZIPInputStream(dir.resolve(file).inputStream()).readBytes().decodeToString()
            qid to runCatching { parser.parse(text) }.getOrNull()
        }
        var usvmSolver: KSolver<KSolverConfiguration>? = if ("usvm" in modes) mk(solverName, ctx) else null
        var incrSolver: KSolver<KSolverConfiguration>? = if ("incr" in modes) mk(solverName, ctx) else null
        var incrStack = ArrayList<KExpr<KBoolSort>>()
        var incrDepth = 0
        try {
            for ((qid, exprs) in parsed) {
                if (exprs == null) {
                    emit("\"qid\":$qid,\"err\":1")
                    // the incremental stack is no longer valid for the next query
                    continue
                }
                val sb = StringBuilder("\"qid\":$qid,\"n\":${exprs.size}")
                try {
                // "fresh2" repeats the fresh mode once more to measure run-to-run variation
                for (fm in listOf("fresh" to "f", "fresh2" to "g")) {
                    if (fm.first !in modes) continue
                    val t = Timing()
                    val c0 = System.nanoTime()
                    val s = mk(solverName, ctx)
                    t.create += System.nanoTime() - c0
                    try {
                        val a0 = System.nanoTime()
                        s.assert(exprs)
                        t.assertN += System.nanoTime() - a0
                        checkAndModel(s, t, timeoutMs)
                    } finally {
                        val d0 = System.nanoTime()
                        s.close()
                        t.create += System.nanoTime() - d0
                    }
                    sb.append(",").append(t.json(fm.second))
                }
                if (usvmSolver != null) {
                    val s = usvmSolver
                    val t = Timing()
                    val p0 = System.nanoTime()
                    s.push()
                    t.pushPop += System.nanoTime() - p0
                    val a0 = System.nanoTime()
                    s.assert(exprs)
                    t.assertN += System.nanoTime() - a0
                    checkAndModel(s, t, timeoutMs)
                    val q0 = System.nanoTime()
                    s.pop()
                    t.pushPop += System.nanoTime() - q0
                    sb.append(",").append(t.json("u"))
                }
                if (incrSolver != null) {
                    val s = incrSolver
                    val t = Timing()
                    var k = 0
                    while (k < exprs.size && k < incrStack.size && exprs[k] == incrStack[k]) k++
                    val p0 = System.nanoTime()
                    if (incrStack.size > k) {
                        s.pop((incrStack.size - k).toUInt())
                        while (incrStack.size > k) incrStack.removeAt(incrStack.size - 1)
                    }
                    t.pushPop += System.nanoTime() - p0
                    for (j in k until exprs.size) {
                        val q0 = System.nanoTime()
                        s.push()
                        t.pushPop += System.nanoTime() - q0
                        val a0 = System.nanoTime()
                        s.assert(exprs[j])
                        t.assertN += System.nanoTime() - a0
                        incrStack += exprs[j]
                    }
                    checkAndModel(s, t, timeoutMs)
                    sb.append(",").append(t.json("i")).append(",\"ik\":$k")
                }
                emit(sb.toString())
                } catch (e: Throwable) {
                    // e.g. KSolverUnsupportedFeatureException (fp.to_ieee_bv): record the failure and restart the persistent solvers
                    emit("\"qid\":$qid,\"err\":2,\"msg\":\"${e.javaClass.simpleName}\"")
                    runCatching { usvmSolver?.close() }
                    runCatching { incrSolver?.close() }
                    usvmSolver = if ("usvm" in modes) mk(solverName, ctx) else null
                    incrSolver = if ("incr" in modes) mk(solverName, ctx) else null
                    incrStack = ArrayList()
                }
            }
        } finally {
            usvmSolver?.close()
            incrSolver?.close()
        }
    }
}
