package org.usvm.machine

import com.microsoft.z3.Version
import io.github.cvc5.Solver
import io.ksmt.KContext
import io.ksmt.solver.cvc5.KCvc5Solver
import io.ksmt.solver.z3.KZ3Solver
import org.usvm.solver.ClientStats
import java.io.File
import java.security.MessageDigest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

internal object Live141Backend {
    val arm = System.getenv("LIVE141_ARM").orEmpty()
    private var initial = emptyMap<String, String>()
    private var effective = emptyMap<String, String>()
    private var version = ""
    private var optionBefore = ""
    private var javaApiSource = ""
    private var adapterSource = ""
    private var z3Help = ""
    private var dumpWritten = false

    private val loadCvc5 by lazy {
        val file = File(System.getProperty("live141.cvc5.jni")).canonicalFile
        check(System.getProperty("cvc5.skipLibraryLoad") == "true")
        System.load(file.path)
    }

    fun cvc5(ctx: KContext): KCvc5Solver {
        check(arm == "cvc5-default" || arm == "cvc5-none")
        loadCvc5
        val solver = KCvc5Solver(ctx)
        try {
            val native = solver.nativeSolver()
            val before = native.getOption("simplification")
            if (arm == "cvc5-none") native.setOption("simplification", "none")
            check(native.getOption("simplification") == if (arm == "cvc5-none") "none" else before)
            if (version.isEmpty()) {
                version = native.getVersion()
                check(version == "1.4.1") { "Expected native cvc5 1.4.1, loaded $version" }
                optionBefore = before
                javaApiSource = Solver::class.java.protectionDomain.codeSource.location.toString()
                adapterSource = KCvc5Solver::class.java.protectionDomain.codeSource.location.toString()
                initial = options(native)
                ClientStats.afterFirstCheck = { effective = options(native) }
            }
            return solver
        } catch (e: Throwable) {
            solver.close()
            throw e
        }
    }

    fun defaultZ3(ctx: KContext): KZ3Solver = KZ3Solver(ctx).apply {
        configure {
            optimizeForTheories(setOf(io.ksmt.solver.KTheory.Array, io.ksmt.solver.KTheory.BV,
                io.ksmt.solver.KTheory.UF, io.ksmt.solver.KTheory.FP))
        }
        recordZ3(this)
    }

    private fun recordZ3(solver: KZ3Solver) {
        if (version.isNotEmpty()) return
        check(arm == "z3")
        val native = solver.nativeSolver()
        version = Version.getString()
        javaApiSource = Version::class.java.protectionDomain.codeSource.location.toString()
        adapterSource = KZ3Solver::class.java.protectionDomain.codeSource.location.toString()
        z3Help = native.help
    }

    private fun options(solver: Solver): Map<String, String> =
        solver.optionNames.sorted().associateWith { key ->
            runCatching { solver.getOption(key) }.getOrElse { e -> "ERROR ${e.message}" }
        }

    private fun sha(file: File): String {
        val hash = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(128 * 1024)
        file.inputStream().use { stream ->
            while (true) {
                val n = stream.read(buf)
                if (n == -1) break
                hash.update(buf, 0, n)
            }
        }
        return java.util.HexFormat.of().formatHex(hash.digest())
    }

    fun dump(directory: File) {
        check(!dumpWritten && version.isNotEmpty())
        if (arm.startsWith("cvc5")) check(effective.isNotEmpty()) { "No timed native check observed" }
        val maps = File("/proc/self/maps").readLines().filter {
            it.contains("libcvc5") || it.contains("/libz3")
        }
        File(directory, "backend-maps.txt").writeText(maps.joinToString("\n", postfix = "\n"))
        val paths = maps.map { it.substring(it.indexOf('/')).removeSuffix(" (deleted)") }.distinct()
        val libraries = paths.associateWith { sha(File(it)) }
        if (arm == "z3") check(libraries.keys.map { File(it).name }.toSet() == setOf("libz3.so", "libz3java.so"))
        else check(libraries.keys == setOf(File(System.getProperty("live141.cvc5.jni")).canonicalPath))
        val affinity = File("/proc/self/status").useLines { lines ->
            lines.first { it.startsWith("Cpus_allowed_list:") }.substringAfter(':').trim()
        }
        val metadata = buildJsonObject {
            put("arm", arm)
            put("version", version)
            put("cpu", affinity)
            put("java_api", javaApiSource)
            put("adapter", adapterSource)
            put("java_runtime", System.getProperty("java.runtime.version"))
            put("processors", Runtime.getRuntime().availableProcessors())
            put("simplification_before_override", optionBefore)
            put("libraries", Json.encodeToJsonElement(kotlinx.serialization.serializer<Map<String, String>>(), libraries))
            put("initial_options", Json.encodeToJsonElement(kotlinx.serialization.serializer<Map<String, String>>(), initial))
            put("after_first_native_check",
                Json.encodeToJsonElement(kotlinx.serialization.serializer<Map<String, String>>(), effective))
        }
        File(directory, "backend.json").writeText(metadata.toString() + "\n")
        if (arm == "z3") File(directory, "z3-help.txt").writeText(z3Help)
        dumpWritten = true
    }
}
