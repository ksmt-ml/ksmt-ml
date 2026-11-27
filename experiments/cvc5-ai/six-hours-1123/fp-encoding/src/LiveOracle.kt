package org.usvm.dataset

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import org.jacodb.api.jvm.JcClassType
import org.jacodb.api.jvm.JcClasspath
import org.jacodb.api.jvm.JcMethod
import org.usvm.CoverageZone
import org.usvm.PathSelectionStrategy
import org.usvm.StateCollectionStrategy
import org.usvm.UMachineOptions
import org.usvm.api.util.JcTestInterpreter
import org.usvm.machine.JcMachine
import org.usvm.machine.Live141Backend
import org.usvm.samples.JacoDBContainer
import org.usvm.samples.samplesWithApproximationsKey
import org.usvm.solver.ClientStats
import java.io.File
import java.lang.management.ManagementFactory
import java.net.URLClassLoader
import java.time.Instant
import kotlin.time.Duration.Companion.milliseconds
import kotlin.time.Duration.Companion.seconds

private val warmups = listOf(
    "warm/livefixture.Control.scalar(I)I",
    "warm/livefixture.Control.array([II)I",
    "warm/livefixture.Control.floating(F)I",
)

fun main(args: Array<String>) {
    val cfg = args.associate { it.substringBefore('=') to it.substringAfter('=') }
    val directory = File(cfg.getValue("out"))
    val events = File(directory, "events.tsv")
    val runStart = System.nanoTime()
    fun event(label: String, tag: String = "") {
        events.appendText("$label\t${Instant.now()}\t${(System.nanoTime() - runStart) / 1e6}\t$tag\n")
    }
    event("driver_start")
    if (cfg["contract"] == "1") {
        solverContract(directory)
        return
    }

    org.burningwave.core.assembler.StaticComponentContainer.Modules.exportAllToAll()
    val fixture = File(cfg.getValue("fixture"))
    val cpFiles = cfg["cp"].orEmpty().split(File.pathSeparator).filter { it.isNotEmpty() }.map(::File)
    val opts = UMachineOptions(
        pathSelectionStrategies = listOf(cfg["selector"]?.let(PathSelectionStrategy::valueOf) ?: PathSelectionStrategy.FORK_DEPTH),
        randomSeed = cfg.getValue("seed").toLong(),
        coverageZone = CoverageZone.TRANSITIVE,
        exceptionsPropagation = true,
        timeout = if (cfg["budget"] == "fixed") kotlin.time.Duration.INFINITE else 6000.milliseconds,
        solverTimeout = 1600.milliseconds,
        typeOperationsTimeout = 1.seconds,
        stepLimit = cfg["steps"]?.toULong() ?: 4000uL,
        stepsFromLastCovered = 3500L,
        runSolverInAnotherProcess = false,
        throwExceptionOnStepFailure = false,
        stopOnCoverage = 0,
        stateCollectionStrategy = StateCollectionStrategy.ALL,
    )
    File(directory, "usvm-options.txt").writeText("$opts\n")
    event("index_start")
    val container = JacoDBContainer(samplesWithApproximationsKey, cpFiles + fixture)
    val cp = container.cp
    event("index_end")
    event("warmup_start")
    for (tag in warmups) {
        val row = measure(cp, tag, opts)
        File(directory, "warmup.jsonl").appendText(row.first + "\n")
        check(row.second) { "Warmup failed on $tag" }
    }
    event("warmup_end")
    Live141Backend.dump(directory)
    event("identity_end")
    if (cfg["smoke"] == "1") {
        liveSmoke(cp, fixture, opts)
        event("live_smoke_pass")
    }
    if (cfg.containsKey("methods")) {
        val requested = File(cfg.getValue("methods")).readLines()
        val results = File(directory, "results.jsonl")
        event("batch_start")
        for (tag in requested) {
            event("method_start", tag)
            println("STOCK_METHOD_START $tag")
            val (row) = measure(cp, tag, opts)
            results.appendText(row + "\n")
            event("method_end", tag)
            println("STOCK_METHOD_END $tag")
            System.gc()
            check(rssMb() <= 3200) { "JVM RSS guard: ${rssMb()} MiB" }
        }
        event("batch_end")
    }
    event("teardown_start")
    runBlocking {
        cp.close()
        container.db.close()
    }
    event("driver_end")
    System.exit(0)
}

private fun find(cp: JcClasspath, tag: String): JcMethod {
    val path = tag.substringAfter('/')
    val start = path.indexOf('(')
    val dot = path.lastIndexOf('.', start)
    val cls = checkNotNull(cp.findClassOrNull(path.substring(0, dot))) { "Class not found $tag" }
    return cls.declaredMethods.single { it.name == path.substring(dot + 1, start) &&
        it.description == path.substring(start) }
}

private fun measure(cp: JcClasspath, tag: String, opts: UMachineOptions): Pair<String, Boolean> {
    ClientStats.reset()
    val os = ManagementFactory.getOperatingSystemMXBean() as com.sun.management.OperatingSystemMXBean
    val cpu = os.processCpuTime
    val start = System.nanoTime()
    var init = 0L
    var analysis = 0L
    var count = 0
    var status = "ok"
    var message = ""
    val dataWitness = mutableListOf<kotlinx.serialization.json.JsonObject>()
    try {
        val m = find(cp, tag)
        JcMachine(cp, opts).use { machine ->
            init = System.nanoTime() - start
            val t0 = System.nanoTime()
            val states = machine.analyze(m)
            count = states.size
            analysis = System.nanoTime() - t0
            if (tag.startsWith("ieee/")) {
                val inputClass = m.enclosingClass.name
                val type = cp.findTypeOrNull(inputClass) as JcClassType
                val typed = type.declaredMethods.single { it.method == m }
                val library = File(requireNotNull(System.getenv("SMT_ORACLE_FIXTURE")))
                URLClassLoader(arrayOf(library.toURI().toURL()), ClassLoader.getPlatformClassLoader()).use { loader ->
                    val resolver = JcTestInterpreter(loader)
                    val signature = java.lang.invoke.MethodType.fromMethodDescriptorString(m.description, loader)
                    val target = loader.loadClass(inputClass).getMethod(m.name, *signature.parameterArray())
                    for (state in states.take(64)) {
                        val proof = buildJsonObject {
                            try {
                                val test = resolver.resolve(typed, state)
                                val symbolic = test.result.getOrThrow() as Int
                                val concrete = target.invoke(null, test.before.parameters[0]) as Int
                                put("expected", symbolic)
                                put("actual", concrete)
                                put("valid", concrete == symbolic)
                            } catch (e: Exception) {
                                put("error", e.javaClass.name)
                                put("message", e.message.orEmpty().take(160))
                            }
                        }
                        dataWitness.add(proof)
                    }
                }
            }
        }
    } catch (e: Throwable) {
        status = "error:" + e.javaClass.simpleName
        message = e.message.orEmpty().take(400)
        System.err.println("METHOD_ERROR $tag $e")
        e.printStackTrace(System.err)
    }
    val wall = (System.nanoTime() - start) / 1e6
    val cpuMs = (os.processCpuTime - cpu) / 1e6
    val stats = Json.parseToJsonElement(ClientStats.snapshotAndReset()).jsonObject
    val row = buildJsonObject {
        put("tag", tag)
        put("arm", Live141Backend.arm)
        put("status", status)
        put("message", message)
        put("wall_ms", wall)
        put("cpu_ms", cpuMs)
        put("construct_ms", init / 1e6)
        put("analyze_ms", analysis / 1e6)
        put("states", count)
        put("stats", stats)
        put("witnesses", kotlinx.serialization.json.JsonArray(dataWitness))
    }
    return row.toString() to (status == "ok")
}

private fun liveSmoke(cp: JcClasspath, fixture: File, opts: UMachineOptions) {
    val m = find(cp, warmups.first())
    val typed = (cp.findTypeOrNull("livefixture.Control") as JcClassType).declaredMethods.single { it.method == m }
    URLClassLoader(arrayOf(fixture.toURI().toURL()), JcMachine::class.java.classLoader).use { loader ->
        val control = loader.loadClass("livefixture.Control").getMethod("scalar", Int::class.javaPrimitiveType)
        val resolver = JcTestInterpreter(loader)
        val actual = JcMachine(cp, opts.copy(stopOnCoverage = 0, stateCollectionStrategy = StateCollectionStrategy.ALL)).use { machine ->
            machine.analyze(m).map { state ->
                val test = resolver.resolve(typed, state)
                val x = test.before.parameters.single() as Int
                val y = test.result.getOrThrow() as Int
                check(control.invoke(null, x) == y) { "Witness disagrees at x=$x, y=$y" }
                println("WITNESS scalar x=$x returns=$y")
                y
            }.toSet()
        }
        check(actual == setOf(-11, 22, 33)) { "Unexpected feasible Java outcomes: $actual" }
    }
    println("LIVE_SMOKE_PASS three feasible returns and no infeasible return")
}

private fun rssMb() =
    File("/proc/self/status").useLines { it.first { r -> r.startsWith("VmRSS:") } }
        .split(Regex("\\s+"))[1].toLong() / 1024
