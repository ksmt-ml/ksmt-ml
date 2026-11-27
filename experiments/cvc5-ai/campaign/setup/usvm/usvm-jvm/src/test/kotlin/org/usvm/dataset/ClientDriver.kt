package org.usvm.dataset

import org.jacodb.api.jvm.JcClassOrInterface
import org.jacodb.api.jvm.JcMethod
import org.usvm.CoverageZone
import org.usvm.PathSelectionStrategy
import org.usvm.UMachineOptions
import org.usvm.machine.JcMachine
import org.usvm.samples.JacoDBContainer
import org.usvm.samples.samplesWithApproximationsKey
import org.usvm.solver.ClientStats
import java.io.File
import kotlin.time.Duration.Companion.milliseconds
import kotlin.time.Duration.Companion.seconds

/**
 * Experiment driver (not part of upstream USVM): analyses a fixed list of methods and records per-method solver statistics.
 * Arguments are key=value: project=NAME cp=a.jar:b.jar methods=FILE (one method tag per line, as in the query dataset)
 * out=FILE.jsonl methodTimeoutMs=N solverTimeoutMs=N totalSeconds=N deadlineEpoch=N rssLimitMb=N.
 * The solver is chosen by the environment variable USVM_SOLVER (z3 by default, cvc5), statistics need USVM_CLIENT_STATS=1.
 */
fun main(args: Array<String>) {
    org.burningwave.core.assembler.StaticComponentContainer.Modules.exportAllToAll()
    val a = args.associate { it.substringBefore('=') to it.substringAfter('=') }
    val project = a.getValue("project")
    val cpFiles = a.getValue("cp").split(File.pathSeparator).map { File(it) }
    val tags = File(a.getValue("methods")).readLines().filter { it.isNotBlank() }
    val out = File(a.getValue("out"))
    val methodTimeout = (a["methodTimeoutMs"] ?: "10000").toLong()
    val solverTimeoutMs = (a["solverTimeoutMs"] ?: "3000").toLong()
    val deadlineEpochMs = a["deadlineEpoch"]?.toLong()?.times(1000)
        ?: (System.currentTimeMillis() + (a["totalSeconds"] ?: "1500").toLong() * 1000)
    val rssLimitMb = (a["rssLimitMb"] ?: "2200").toLong()
    val solverName = System.getenv("USVM_SOLVER") ?: "z3"

    val done: Set<String> = if (out.exists()) {
        out.readLines().map { it.substringAfter("\"tag\":\"").substringBefore("\",\"solver\"") }.toSet()
    } else {
        emptySet()
    }

    val container = JacoDBContainer(samplesWithApproximationsKey, cpFiles)
    val cp = container.cp
    var n = 0
    for (tag in tags) {
        if (System.currentTimeMillis() > deadlineEpochMs) {
            System.err.println("[client] deadline reached")
            break
        }
        if (tag in done) continue
        val m = findMethod(cp, project, tag)
        if (m == null) {
            System.err.println("[client] method not found: $tag")
            continue
        }
        runMethod(project, tag, solverName, m, methodTimeout, solverTimeoutMs, out)
        n++
        System.gc()
        if (rssMb() > rssLimitMb) {
            System.err.println("[client] RSS limit reached, restart requested")
            System.exit(3)
        }
    }
    System.err.println("[client] DONE $project solver=$solverName methods=$n")
    System.exit(0)
}

private fun findMethod(cp: org.jacodb.api.jvm.JcClasspath, project: String, tag: String): JcMethod? {
    val rest = tag.removePrefix("$project/")
    val paren = rest.indexOf('(')
    val dot = rest.lastIndexOf('.', paren)
    val className = rest.substring(0, dot)
    val name = rest.substring(dot + 1, paren)
    val desc = rest.substring(paren)
    val jc: JcClassOrInterface = runCatching { cp.findClassOrNull(className) }.getOrNull() ?: return null
    return jc.declaredMethods.firstOrNull { it.name == name && it.description == desc }
}

private fun rssMb(): Long =
    File("/proc/self/status").readLines().first { it.startsWith("VmRSS:") }.split(Regex("\\s+"))[1].toLong() / 1024

private fun runMethod(
    project: String,
    tag: String,
    solverName: String,
    m: JcMethod,
    timeoutMs: Long,
    solverTimeoutMs: Long,
    out: File,
) {
    ClientStats.reset()
    val options = UMachineOptions(
        pathSelectionStrategies = listOf(PathSelectionStrategy.FORK_DEPTH),
        coverageZone = CoverageZone.TRANSITIVE,
        exceptionsPropagation = true,
        timeout = timeoutMs.milliseconds,
        stepsFromLastCovered = 3500L,
        solverTimeout = solverTimeoutMs.milliseconds,
        typeOperationsTimeout = 1.seconds,
    )
    val os = java.lang.management.ManagementFactory.getOperatingSystemMXBean() as com.sun.management.OperatingSystemMXBean
    val cpu0 = os.processCpuTime
    val load1 = File("/proc/loadavg").readText().substringBefore(' ')
    val t0 = System.nanoTime()
    var tCtor = 0L
    var tAnalyze = 0L
    var status = "ok"
    var states = 0
    try {
        val c0 = System.nanoTime()
        JcMachine(m.enclosingClass.classpath, options).use { machine ->
            tCtor = System.nanoTime() - c0
            val a0 = System.nanoTime()
            states = machine.analyze(m).size
            tAnalyze = System.nanoTime() - a0
        }
    } catch (e: Throwable) {
        status = "error:" + e.javaClass.simpleName
        System.err.println("[client] ERROR in $tag: $e " + e.cause)
    }
    val wallMs = (System.nanoTime() - t0) / 1e6
    val cpuMs = (os.processCpuTime - cpu0) / 1e6
    val stats = ClientStats.snapshotAndReset()
    val esc = tag.replace("\\", "\\\\").replace("\"", "\\\"")
    synchronized(out) {
        out.appendText(
            "{\"tag\":\"$esc\",\"solver\":\"$solverName\",\"project\":\"$project\",\"status\":\"$status\",\"states\":$states," +
                "\"wall_ms\":$wallMs,\"cpu_ms\":$cpuMs,\"load1\":$load1,\"ctor_ms\":${tCtor / 1e6},\"analyze_ms\":${tAnalyze / 1e6},\"stats\":$stats}\n"
        )
    }
}
