import io.ksmt.KContext
import io.ksmt.KContext.SimplificationMode.NO_SIMPLIFY
import io.ksmt.ml.dataset.DatasetFeatureExtractor
import io.ksmt.ml.features.KsmtAstFeatureInput
import io.ksmt.ml.features.MachSmtFeatureExtractor
import io.ksmt.ml.features.MachSmtFeatureJson
import io.ksmt.ml.features.SmtLibSyntaxScanner
import io.ksmt.solver.KSolverStatus
import io.ksmt.solver.KTheory
import io.ksmt.solver.cvc5.KCvc5Solver
import io.ksmt.solver.z3.KZ3SMTLibParser
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import kotlin.time.Duration.Companion.seconds

private const val REPETITIONS = 3

private data class Cvc5Config(val name: String, val options: Map<String, String>)

private data class FormulaCase(
        val name: String,
        val theories: Set<KTheory>,
        val build: (KContext, KCvc5Solver) -> Unit
)

private data class RunResult(
        val formula: String,
        val configuration: String,
        val repetition: Int,
        val options: String,
        val status: String,
        val elapsedMillis: Double,
        val details: String
)

fun main(args: Array<String>) {
    when (args.firstOrNull()) {
        null, "--help", "-h" -> printUsage()

        "--extract-dataset" -> {
            require(args.size in 6..7) {
                "Usage: --extract-dataset DATASET_ID DATASET_ROOT OUTPUT.csv[.gz] " +
                    "INPUT_MANIFEST CODE_REVISION [WORKERS]"
            }

            val summary = DatasetFeatureExtractor.extract(
                    datasetId = args[1],
                    inputRoot = Paths.get(args[2]),
                    outputPath = Paths.get(args[3]),
                    inputManifestPath = Paths.get(args[4]),
                    codeRevision = args[5],
                    workers = args.getOrNull(6)?.toInt()
                            ?: Runtime.getRuntime().availableProcessors().coerceIn(1, 4)
            )
            println("dataset: ${summary.inputRoot}")
            println("features: ${summary.outputPath}")
            println("schema: ${summary.schemaPath}")
            println("manifest: ${summary.manifestPath}")
            println(
                    "total: ${summary.total}, successful: ${summary.successful}, " +
                            "partial: ${summary.partial}, failed: ${summary.failed}"
            )
            check(summary.failed == 0) {
                "Dataset extraction completed with ${summary.failed} failed inputs"
            }
        }

        "--extract-features" -> {
            require(args.size in 2..3) {
                "Usage: --extract-features /path/to/file.smt2 [output.json]"
            }

            extractFeatures(
                    benchmarkPath = Paths.get(args[1]),
                    outputPath = args.getOrNull(2)?.let(Paths::get)
            )
        }

        "--benchmark" -> {
            require(args.size == 2) { "Usage: --benchmark /path/to/file.smt2" }
            runBenchmark(Paths.get(args[1]))
        }

        "--list-options" -> {
            require(args.size == 1) { "Usage: --list-options" }
            printCvc5Options()
        }

        "--smoke-experiments" -> {
            require(args.size == 1) { "Usage: --smoke-experiments" }
            runExperiments()
        }

        else -> error("Unknown command: ${args.first()}\n\n${usage()}")
    }
}

private fun printUsage() = println(usage())

private fun usage(): String = """
    ksmt-ml experimental command-line tools

    Usage:
      --extract-features INPUT.smt2 [OUTPUT.json]
      --extract-dataset DATASET_ID DATASET_ROOT OUTPUT.csv[.gz] INPUT_MANIFEST CODE_REVISION [WORKERS]
      --benchmark INPUT.smt2
      --list-options
      --smoke-experiments
      --help
""".trimIndent()

private fun extractFeatures(benchmarkPath: Path, outputPath: Path?) {
    require(Files.isRegularFile(benchmarkPath)) { "Benchmark does not exist: $benchmarkPath" }

    val source = Files.readString(benchmarkPath)
    val syntaxTokenCounts = SmtLibSyntaxScanner.countTokens(source)

    val result = KContext(simplificationMode = NO_SIMPLIFY).use { ctx ->
        val assertions = KZ3SMTLibParser(ctx).parse(benchmarkPath)
        MachSmtFeatureExtractor().extract(
                KsmtAstFeatureInput(
                        assertions = assertions,
                        syntaxTokenCounts = syntaxTokenCounts,
                        sourceSizeBytes = Files.size(benchmarkPath),
                        sourceName = benchmarkPath.fileName.toString()
                )
        )
    }

    val json = MachSmtFeatureJson.encode(result)
    if (outputPath == null) {
        print(json)
    } else {
        outputPath.toAbsolutePath().parent?.let(Files::createDirectories)
        Files.writeString(outputPath, json)
        println(outputPath.toAbsolutePath().normalize())
    }
}

private fun runBenchmark(benchmarkPath: Path) {
    require(Files.isRegularFile(benchmarkPath)) { "Benchmark does not exist: $benchmarkPath" }

    KContext().use { ctx ->
        val parseStartedAt = System.nanoTime()

        val assertions = KZ3SMTLibParser(ctx).parse(benchmarkPath)

        val parseMillis = (System.nanoTime() - parseStartedAt) / 1_000_000.0

        KCvc5Solver(ctx).use { solver ->
            // Configure the solver before the first assertion. The default
            // baseline deliberately leaves the configuration empty.
            solver.configure {
                // Example:
                // setCvc5Option("seed", "42")
            }

            val assertStartedAt = System.nanoTime()

            solver.assert(assertions)

            val assertMillis = (System.nanoTime() - assertStartedAt) / 1_000_000.0

            val solveStartedAt = System.nanoTime()

            val status = solver.check(10.seconds)

            val solveMillis = (System.nanoTime() - solveStartedAt) / 1_000_000.0

            println("file:       ${benchmarkPath.toAbsolutePath()}")
            println("assertions: ${assertions.size}")
            println("status:     $status")
            println("parse:      $parseMillis ms")
            println("assert:     $assertMillis ms")
            println("solve:      $solveMillis ms")

            if (status == KSolverStatus.UNKNOWN) {
                println("reason:     ${solver.reasonOfUnknown()}")
            }
        }
    }
}

private fun runExperiments() {
    val configurations = createConfigurations()
    val formulas = createFormulas()
    val results = mutableListOf<RunResult>()

    KContext().use { ctx ->
        for (formula in formulas) {
            for (configuration in configurations) {
                repeat(REPETITIONS) { repetitionIndex ->
                    val repetition = repetitionIndex + 1

                    val result =
                            runSingleExperiment(
                                    ctx = ctx,
                                    formula = formula,
                                    configuration = configuration,
                                    repetition = repetition
                            )

                    results += result

                    println(
                            "${result.formula}\t" +
                                    "${result.configuration}\t" +
                                    "run=${result.repetition}\t" +
                                    "${result.status}\t" +
                                    "${result.elapsedMillis} ms"
                    )
                }
            }
        }
    }

    saveResults(results)
}

private fun runSingleExperiment(
        ctx: KContext,
        formula: FormulaCase,
        configuration: Cvc5Config,
        repetition: Int
): RunResult {
    val optionsAsString =
            configuration.options.entries.joinToString(";") { "${it.key}=${it.value}" }

    return try {
        // Create a fresh solver for every experiment.
        KCvc5Solver(ctx).use { solver ->
            solver.configure {
                optimizeForTheories(theories = formula.theories, quantifiersAllowed = false)

                configuration.options.forEach { (name, value) -> setCvc5Option(name, value) }
            }

            // Expression construction, internalization, and assertion time are
            // deliberately excluded from the measured solve time.
            formula.build(ctx, solver)

            val startedAt = System.nanoTime()
            val status = solver.check(5.seconds)
            val elapsedNanos = System.nanoTime() - startedAt
            val elapsedMillis = elapsedNanos / 1_000_000.0

            val details =
                    if (status == KSolverStatus.UNKNOWN) {
                        solver.reasonOfUnknown()
                    } else {
                        ""
                    }

            RunResult(
                    formula = formula.name,
                    configuration = configuration.name,
                    repetition = repetition,
                    options = optionsAsString,
                    status = status.toString(),
                    elapsedMillis = elapsedMillis,
                    details = details
            )
        }
    } catch (error: Exception) {
        RunResult(
                formula = formula.name,
                configuration = configuration.name,
                repetition = repetition,
                options = optionsAsString,
                status = "ERROR",
                elapsedMillis = Double.NaN,
                details = "${error::class.simpleName}: ${error.message}"
        )
    }
}

private fun createConfigurations(): List<Cvc5Config> =
        listOf(
                Cvc5Config(name = "baseline", options = emptyMap()),
                Cvc5Config(name = "seed-1", options = mapOf("seed" to "1")),
                Cvc5Config(name = "seed-42", options = mapOf("seed" to "42")),
                Cvc5Config(
                        name = "decision-justification",
                        options = mapOf("decision" to "justification")
                ),
                Cvc5Config(name = "decision-stoponly", options = mapOf("decision" to "stoponly")),
                Cvc5Config(
                        name = "combined",
                        options =
                                mapOf(
                                        "seed" to "42",
                                        "decision" to "justification",
                                        "simplification" to "none"
                                )
                )
        )

private fun createFormulas(): List<FormulaCase> =
        listOf(
                FormulaCase(
                        name = "propositional",
                        theories = emptySet(),
                        build = { ctx, solver ->
                            with(ctx) {
                                val a = mkConst("prop_a", boolSort)
                                val b = mkConst("prop_b", boolSort)

                                solver.assert(a or b)
                                solver.assert(a implies !b)
                            }
                        }
                ),
                FormulaCase(
                        name = "linear-integer-arithmetic",
                        theories = setOf(KTheory.LIA),
                        build = { ctx, solver ->
                            with(ctx) {
                                val x = mkConst("lia_x", intSort)
                                val y = mkConst("lia_y", intSort)

                                solver.assert(x gt 0.expr)
                                solver.assert(y eq x + 1.expr)
                                solver.assert(y lt 100.expr)
                            }
                        }
                ),
                FormulaCase(
                        name = "bit-vectors",
                        theories = setOf(KTheory.BV),
                        build = { ctx, solver ->
                            with(ctx) {
                                val a = mkConst("bv_a", bv32Sort)
                                val b = mkConst("bv_b", bv32Sort)

                                // a + b = a implies b = 0 modulo 2^32.
                                solver.assert(mkBvAddExpr(a, b) eq a)

                                // a must be different from b.
                                solver.assert(a neq b)
                            }
                        }
                ),
                FormulaCase(
                        name = "bit-vector-array",
                        theories = setOf(KTheory.Array, KTheory.BV),
                        build = { ctx, solver ->
                            with(ctx) {
                                val arraySort = mkArraySort(bv32Sort, bv32Sort)

                                val memory = mkConst("memory", arraySort)
                                val address = mkConst("address", bv32Sort)
                                val value = mkConst("value", bv32Sort)

                                solver.assert(memory.select(address) eq value)
                                solver.assert(value neq address)
                            }
                        }
                )
        )

private fun saveResults(results: List<RunResult>) {
    val outputPath = Paths.get("build", "results", "cvc5-experiments.csv")

    Files.createDirectories(outputPath.parent)

    val lines = buildList {
        add("formula,configuration,repetition,options," + "status,elapsed_ms,details")

        results.forEach { result ->
            add(
                    listOf(
                                    result.formula,
                                    result.configuration,
                                    result.repetition.toString(),
                                    result.options,
                                    result.status,
                                    result.elapsedMillis.toString(),
                                    result.details
                            )
                            .joinToString(",") { csvEscape(it) }
            )
        }
    }

    Files.write(outputPath, lines)

    println()
    println("Results saved to:")
    println(outputPath.toAbsolutePath())
}

private fun csvEscape(value: String): String {
    val escaped = value.replace("\"", "\"\"")
    return "\"$escaped\""
}

private fun printCvc5Options() {
    KContext().use { ctx ->
        KCvc5Solver(ctx).use { solver ->
            val cvc5 = solver.nativeSolver()

            println("cvc5 version: ${cvc5.getVersion()}")
            println("option count: ${cvc5.getOptionNames().size}")
            println()

            cvc5.getOptionNames().sorted().forEach { optionName ->
                println(cvc5.getOptionInfo(optionName))
            }
        }
    }
}
