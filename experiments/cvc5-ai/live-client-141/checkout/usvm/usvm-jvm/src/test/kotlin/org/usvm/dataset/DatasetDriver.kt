package org.usvm.dataset

import org.jacodb.api.jvm.JcClassOrInterface
import org.jacodb.api.jvm.JcMethod
import org.usvm.CoverageZone
import org.usvm.PathSelectionStrategy
import org.usvm.UMachineOptions
import org.usvm.machine.JcMachine
import org.usvm.samples.JacoDBContainer
import org.usvm.samples.samplesWithApproximationsKey
import org.usvm.solver.SmtQueryLog
import java.io.File
import java.util.zip.ZipFile
import kotlin.random.Random
import kotlin.time.Duration.Companion.milliseconds
import kotlin.time.Duration.Companion.seconds

/**
 * Experiment driver (not part of upstream USVM): runs USVM on public methods of classes from the given jars/dirs
 * and lets [SmtQueryLog] dump every solver query. Arguments are key=value:
 * project=NAME cp=a.jar:b.jar targets=a.jar (jars or dirs whose classes are analysed) pkg=prefix
 * methodTimeoutMs=N totalSeconds=N maxMethodsPerClass=N maxClasses=N seed=N shard=I/K solverTimeoutMs=N
 */
fun main(args: Array<String>) {
    org.burningwave.core.assembler.StaticComponentContainer.Modules.exportAllToAll()
    val a = args.associate { it.substringBefore('=') to it.substringAfter('=') }
    val project = a.getValue("project")
    val cpFiles = a.getValue("cp").split(File.pathSeparator).map { File(it) }
    val targetFiles = a.getValue("targets").split(File.pathSeparator).map { File(it) }
    val pkg = a["pkg"] ?: ""
    val methodTimeout = (a["methodTimeoutMs"] ?: "10000").toLong()
    val totalSeconds = (a["totalSeconds"] ?: "600").toLong()
    val maxPerClass = (a["maxMethodsPerClass"] ?: "6").toInt()
    val maxClasses = (a["maxClasses"] ?: "100000").toInt()
    val solverTimeoutMs = (a["solverTimeoutMs"] ?: "3000").toLong()
    val seed = (a["seed"] ?: "1").toLong()
    val shard = (a["shard"] ?: "0/1").split("/").map { it.toInt() }
    val resultsFile = File(System.getenv("USVM_SMT_DIR") ?: ".", "methods.jsonl")

    val classNames = targetFiles.flatMap { f ->
        if (f.isDirectory) {
            f.walkTopDown().filter { it.extension == "class" }
                .map { it.relativeTo(f).path.removeSuffix(".class").replace('/', '.') }.toList()
        } else {
            ZipFile(f).use { z ->
                z.entries().asSequence().map { it.name }.filter { it.endsWith(".class") }
                    .map { it.removeSuffix(".class").replace('/', '.') }.toList()
            }
        }
    }.filter { it.startsWith(pkg) && !it.contains("module-info") && !it.contains("package-info") }
        .filter { !it.substringAfterLast('.').contains('$') }
        .sorted()
        .filterIndexed { i, _ -> i % shard[1] == shard[0] }

    val container = JacoDBContainer(samplesWithApproximationsKey, cpFiles)
    val cp = container.cp
    val rnd = Random(seed)
    val deadlineEpochMs = a["deadlineEpoch"]?.toLong()?.times(1000) ?: (System.currentTimeMillis() + totalSeconds * 1000)
    val rssLimitMb = (a["rssLimitMb"] ?: "2500").toLong()
    val done: Set<String> = if (resultsFile.exists()) {
        resultsFile.readLines().map { it.substringAfter("\"tag\":\"").substringBefore("\",\"status\"") }.toSet()
    } else {
        emptySet()
    }
    var nMethods = 0
    var nClasses = 0
    for (cn in classNames.shuffled(rnd)) {
        if (nClasses >= maxClasses || System.currentTimeMillis() > deadlineEpochMs) break
        val jc: JcClassOrInterface = runCatching { cp.findClassOrNull(cn) }.getOrNull() ?: continue
        if (jc.isInterface || jc.isAbstract || jc.isAnonymous || jc.isAnnotation || !jc.isPublic) continue
        val methods = jc.declaredMethods.filter {
            it.isPublic && !it.isAbstract && !it.isNative && !it.isConstructor && !it.isClassInitializer &&
                !it.isSynthetic && it.name != "hashCode" && it.name != "equals" && it.name != "toString"
        }.shuffled(rnd).take(maxPerClass)
        if (methods.isEmpty()) continue
        nClasses++
        for (m in methods) {
            if (System.currentTimeMillis() > deadlineEpochMs) break
            val tag = "$project/${m.enclosingClass.name}.${m.name}${m.description}"
            if (tag in done) continue
            runMethod(project, m, methodTimeout, solverTimeoutMs, resultsFile)
            nMethods++
            System.gc()
            if (rssMb() > rssLimitMb) {
                System.err.println("[driver] RSS limit reached, restart requested")
                System.exit(3)
            }
        }
        System.err.println("[driver] $project classes=$nClasses methods=$nMethods last=$cn")
    }
    System.err.println("[driver] DONE $project classes=$nClasses methods=$nMethods")
    System.exit(0)
}

private fun rssMb(): Long =
    File("/proc/self/status").readLines().first { it.startsWith("VmRSS:") }.split(Regex("\\s+"))[1].toLong() / 1024

private fun runMethod(project: String, m: JcMethod, timeoutMs: Long, solverTimeoutMs: Long, results: File) {
    val tag = "$project/${m.enclosingClass.name}.${m.name}${m.description}"
    SmtQueryLog.tag = tag
    val options = UMachineOptions(
        pathSelectionStrategies = listOf(PathSelectionStrategy.FORK_DEPTH),
        coverageZone = CoverageZone.TRANSITIVE,
        exceptionsPropagation = true,
        timeout = timeoutMs.milliseconds,
        stepsFromLastCovered = 3500L,
        solverTimeout = solverTimeoutMs.milliseconds,
        typeOperationsTimeout = 1.seconds,
    )
    val t0 = System.nanoTime()
    var status = "ok"
    var states = 0
    try {
        JcMachine(m.enclosingClass.classpath, options).use { machine ->
            states = machine.analyze(m).size
        }
    } catch (e: Throwable) {
        status = "error:" + e.javaClass.simpleName
        System.err.println("[driver] ERROR in $tag: $e " + e.cause)
    }
    val ms = (System.nanoTime() - t0) / 1_000_000
    val esc = tag.replace("\\", "\\\\").replace("\"", "\\\"")
    synchronized(results) {
        results.appendText("{\"tag\":\"$esc\",\"status\":\"$status\",\"states\":$states,\"wall_ms\":$ms}\n")
    }
}
