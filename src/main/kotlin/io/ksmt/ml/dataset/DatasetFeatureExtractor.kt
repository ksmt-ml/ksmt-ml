package io.ksmt.ml.dataset

import io.ksmt.KContext
import io.ksmt.KContext.SimplificationMode.NO_SIMPLIFY
import io.ksmt.ml.features.KsmtAstFeatureInput
import io.ksmt.ml.features.KSMT_MACHSMT_INSPIRED_FEATURE_COUNT
import io.ksmt.ml.features.KSMT_MACHSMT_INSPIRED_SCHEMA_VERSION
import io.ksmt.ml.features.MachSmtFeatureExtractor
import io.ksmt.ml.features.MachSmtFeatureValue
import io.ksmt.ml.features.SmtLibSyntaxScanner
import io.ksmt.solver.z3.KZ3SMTLibParser
import java.io.BufferedWriter
import java.io.OutputStreamWriter
import java.nio.charset.StandardCharsets
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.time.Instant
import java.util.concurrent.ExecutorCompletionService
import java.util.concurrent.Executors
import java.util.UUID
import java.util.zip.GZIPOutputStream

data class DatasetExtractionSummary(
    val inputRoot: Path,
    val outputPath: Path,
    val schemaPath: Path,
    val manifestPath: Path,
    val total: Int,
    val successful: Int,
    val partial: Int,
    val failed: Int,
)

object DatasetFeatureExtractor {
    fun extract(
        inputRoot: Path,
        outputPath: Path,
        datasetId: String,
        inputManifestPath: Path,
        codeRevision: String,
        workers: Int = defaultWorkers(),
        progressEvery: Int = 1_000,
    ): DatasetExtractionSummary {
        require(Files.isDirectory(inputRoot)) { "Dataset directory does not exist: $inputRoot" }
        require(datasetId.isNotBlank()) { "datasetId must not be blank" }
        require(Files.isRegularFile(inputManifestPath)) {
            "Dataset manifest does not exist: $inputManifestPath"
        }
        require(codeRevision.isNotBlank()) { "codeRevision must not be blank" }
        require(workers > 0) { "workers must be positive" }

        val files = Files.walk(inputRoot).use { paths ->
            paths
                .filter { path -> Files.isRegularFile(path) && path.fileName.toString().endsWith(".smt2") }
                .sorted()
                .toList()
        }
        require(files.isNotEmpty()) { "No .smt2 files found under $inputRoot" }
        files.forEach { path ->
            val relativePath = inputRoot.relativize(path)
            require(relativePath.nameCount >= 3) {
                "Expected LOGIC/FAMILY/FILE.smt2 below the dataset root, got: $relativePath"
            }
        }
        validateInputManifest(inputManifestPath, datasetId, inputRoot, files)

        outputPath.toAbsolutePath().parent?.let(Files::createDirectories)
        val schema = featureSchema()
        val schemaPath = schemaPathFor(outputPath)
        val manifestPath = manifestPathFor(outputPath)
        val runId = UUID.randomUUID().toString()
        val temporaryOutput = temporarySibling(outputPath, runId)
        val temporarySchema = temporarySibling(schemaPath, runId)
        val temporaryManifest = temporarySibling(manifestPath, runId)

        val executor = Executors.newFixedThreadPool(workers)
        val completion = ExecutorCompletionService<IndexedDatasetFeatureRow>(executor)
        val readyRows = hashMapOf<Int, DatasetFeatureRow>()
        var submitted = 0
        var completed = 0
        var successful = 0
        var partial = 0
        var failed = 0
        val maxInFlight = workers * 4

        try {
            writeSchema(temporarySchema, schema)
            outputWriter(temporaryOutput, compressed = outputPath.fileName.toString().endsWith(".gz")).use { writer ->
                writeHeader(writer, schema)

                while (completed < files.size) {
                    while (submitted < files.size && submitted - completed < maxInFlight) {
                        val index = submitted
                        val path = files[submitted++]
                        completion.submit {
                            IndexedDatasetFeatureRow(index, extractOne(inputRoot, path))
                        }
                    }

                    val indexedRow = completion.take().get()
                    readyRows[indexedRow.index] = indexedRow.row

                    while (true) {
                        val row = readyRows.remove(completed) ?: break
                        writeRow(writer, row, datasetId)
                        completed++
                        when (row.status) {
                            "ok" -> successful++
                            "partial" -> partial++
                            else -> failed++
                        }

                        if (progressEvery > 0 &&
                            (completed % progressEvery == 0 || completed == files.size)
                        ) {
                            println(
                                "features: $completed/${files.size}, " +
                                    "partial=$partial, failed=$failed"
                            )
                        }
                    }
                }
            }
            val schemaSha256 = sha256(temporarySchema)
            val outputSha256 = sha256(temporaryOutput)
            writeManifest(
                path = temporaryManifest,
                datasetId = datasetId,
                inputRoot = inputRoot,
                inputManifestPath = inputManifestPath,
                inputManifestSha256 = sha256(inputManifestPath),
                outputPath = outputPath,
                outputSha256 = outputSha256,
                schemaPath = schemaPath,
                schemaSha256 = schemaSha256,
                codeRevision = codeRevision,
                total = completed,
                successful = successful,
                partial = partial,
                failed = failed,
            )

            replaceAtomically(temporarySchema, schemaPath)
            replaceAtomically(temporaryOutput, outputPath)
            replaceAtomically(temporaryManifest, manifestPath)
        } finally {
            executor.shutdownNow()
            Files.deleteIfExists(temporaryOutput)
            Files.deleteIfExists(temporarySchema)
            Files.deleteIfExists(temporaryManifest)
        }

        return DatasetExtractionSummary(
            inputRoot = inputRoot.toAbsolutePath().normalize(),
            outputPath = outputPath.toAbsolutePath().normalize(),
            schemaPath = schemaPath.toAbsolutePath().normalize(),
            manifestPath = manifestPath.toAbsolutePath().normalize(),
            total = completed,
            successful = successful,
            partial = partial,
            failed = failed,
        )
    }

    private fun extractOne(inputRoot: Path, path: Path): DatasetFeatureRow {
        val relativePath = inputRoot.relativize(path)
        val portableRelativePath = relativePath.joinToString("/")
        val logic = relativePath.getName(0).toString()
        val family = if (relativePath.nameCount > 2) relativePath.getName(1).toString() else ""

        return try {
            val sourceBytes = Files.readAllBytes(path)
            val source = sourceBytes.toString(StandardCharsets.UTF_8)
            val tokenCounts = SmtLibSyntaxScanner.countTokens(source)
            val sourceSize = sourceBytes.size.toLong()
            val benchmarkSha256 = sha256(sourceBytes)

            try {
                val result = KContext(simplificationMode = NO_SIMPLIFY).use { context ->
                    val assertions = KZ3SMTLibParser(context).parse(path)
                    MachSmtFeatureExtractor().extract(
                        KsmtAstFeatureInput(
                            assertions = assertions,
                            syntaxTokenCounts = tokenCounts,
                            sourceSizeBytes = sourceSize,
                            sourceName = portableRelativePath,
                        )
                    )
                }

                DatasetFeatureRow(
                    relativePath = portableRelativePath,
                    benchmarkSha256 = benchmarkSha256,
                    logic = logic,
                    family = family,
                    expectedStatus = SmtLibSyntaxScanner.findExpectedStatus(source).orEmpty(),
                    status = "ok",
                    error = "",
                    values = result.vector,
                )
            } catch (error: Exception) {
                val syntaxOnly = MachSmtFeatureExtractor().extract(
                    KsmtAstFeatureInput(
                        assertions = emptyList(),
                        syntaxTokenCounts = tokenCounts,
                        sourceSizeBytes = sourceSize,
                        sourceName = portableRelativePath,
                    )
                )
                DatasetFeatureRow(
                    relativePath = portableRelativePath,
                    benchmarkSha256 = benchmarkSha256,
                    logic = logic,
                    family = family,
                    expectedStatus = SmtLibSyntaxScanner.findExpectedStatus(source).orEmpty(),
                    status = "partial",
                    error = compactError(error),
                    values = syntaxOnly.vector.mapIndexed { index, value ->
                        if (index in STRUCTURAL_FEATURE_INDICES) Double.NaN else value
                    },
                )
            }
        } catch (error: Exception) {
            DatasetFeatureRow(
                relativePath = portableRelativePath,
                benchmarkSha256 = "",
                logic = logic,
                family = family,
                expectedStatus = "",
                status = "error",
                error = compactError(error),
                values = List(FEATURE_COUNT) { Double.NaN },
            )
        }
    }

    private fun featureSchema(): List<MachSmtFeatureValue> =
        MachSmtFeatureExtractor().extract(KsmtAstFeatureInput(assertions = emptyList())).features

    private fun validateInputManifest(
        manifestPath: Path,
        datasetId: String,
        inputRoot: Path,
        files: List<Path>,
    ) {
        val lines = Files.readAllLines(manifestPath, StandardCharsets.UTF_8)
            .filter(String::isNotBlank)
        require(lines.size >= 2) { "Dataset manifest has no data rows: $manifestPath" }

        val header = lines.first().split('\t')
        val datasetIdIndex = header.indexOf("dataset_id")
        val logicIndex = header.indexOf("logic")
        val benchmarksIndex = header.indexOf("benchmarks")
        require(datasetIdIndex >= 0 && logicIndex >= 0 && benchmarksIndex >= 0) {
            "Dataset manifest must have tab-separated dataset_id, logic, and benchmarks columns"
        }

        val expectedByLogic = linkedMapOf<String, Int>()
        lines.drop(1).forEachIndexed { rowIndex, line ->
            val fields = line.split('\t')
            val requiredIndex = maxOf(datasetIdIndex, logicIndex, benchmarksIndex)
            require(fields.size > requiredIndex) {
                "Dataset manifest row ${rowIndex + 2} has fewer columns than its header"
            }
            require(fields[datasetIdIndex] == datasetId) {
                "Dataset manifest row ${rowIndex + 2} belongs to '${fields[datasetIdIndex]}', " +
                    "not '$datasetId'"
            }
            val logic = fields[logicIndex]
            val count = fields[benchmarksIndex].toIntOrNull()
            require(logic.isNotBlank() && count != null && count >= 0) {
                "Invalid logic/count in dataset manifest row ${rowIndex + 2}"
            }
            require(logic !in expectedByLogic) {
                "Duplicate logic '$logic' in dataset manifest"
            }
            expectedByLogic[logic] = count
        }

        val actualByLogic = files.groupingBy { path ->
            inputRoot.relativize(path).getName(0).toString()
        }.eachCount()
        require(actualByLogic == expectedByLogic) {
            "Dataset files do not match manifest counts: expected=$expectedByLogic, actual=$actualByLogic"
        }
    }

    private fun writeHeader(writer: BufferedWriter, schema: List<MachSmtFeatureValue>) {
        val columns = METADATA_COLUMNS + schema.map { it.name }
        writer.appendLine(columns.joinToString(",") { csv(it) })
    }

    private fun writeRow(writer: BufferedWriter, row: DatasetFeatureRow, datasetId: String) {
        val metadata = listOf(
            datasetId,
            KSMT_MACHSMT_INSPIRED_SCHEMA_VERSION,
            row.benchmarkSha256,
            row.relativePath,
            row.logic,
            row.family,
            row.expectedStatus,
            row.status,
            row.error,
        )
        writer.append(metadata.joinToString(",") { csv(it) })
        for (value in row.values) {
            writer.append(',')
            if (!value.isNaN()) writer.append(value.toString())
        }
        writer.appendLine()
    }

    private fun writeSchema(path: Path, schema: List<MachSmtFeatureValue>) {
        path.toAbsolutePath().parent?.let(Files::createDirectories)
        Files.newBufferedWriter(path, StandardCharsets.UTF_8).use { writer ->
            writer.appendLine("schema_version,id,name,cluster,value_type,missing_value")
            schema.forEach { feature ->
                writer.appendLine(
                    listOf(
                        KSMT_MACHSMT_INSPIRED_SCHEMA_VERSION,
                        feature.id.toString(),
                        feature.name,
                        feature.cluster,
                        "double",
                        "NaN",
                    )
                        .joinToString(",") { csv(it) }
                )
            }
        }
    }

    private fun outputWriter(path: Path, compressed: Boolean): BufferedWriter {
        val output = Files.newOutputStream(path)
        val encoded = if (compressed) GZIPOutputStream(output) else output
        return BufferedWriter(OutputStreamWriter(encoded, StandardCharsets.UTF_8))
    }

    private fun schemaPathFor(outputPath: Path): Path {
        val outputName = outputPath.fileName.toString()
        val stem = outputName.removeSuffix(".gz").removeSuffix(".csv")
        return outputPath.resolveSibling("$stem.schema.csv")
    }

    private fun manifestPathFor(outputPath: Path): Path {
        val outputName = outputPath.fileName.toString()
        val stem = outputName.removeSuffix(".gz").removeSuffix(".csv")
        return outputPath.resolveSibling("$stem.manifest.json")
    }

    private fun temporarySibling(path: Path, runId: String): Path =
        path.resolveSibling(".${path.fileName}.tmp-$runId")

    private fun replaceAtomically(source: Path, target: Path) {
        target.toAbsolutePath().parent?.let(Files::createDirectories)
        try {
            Files.move(
                source,
                target,
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
        } catch (_: AtomicMoveNotSupportedException) {
            Files.move(source, target, StandardCopyOption.REPLACE_EXISTING)
        }
    }

    private fun writeManifest(
        path: Path,
        datasetId: String,
        inputRoot: Path,
        inputManifestPath: Path,
        inputManifestSha256: String,
        outputPath: Path,
        outputSha256: String,
        schemaPath: Path,
        schemaSha256: String,
        codeRevision: String,
        total: Int,
        successful: Int,
        partial: Int,
        failed: Int,
    ) {
        val json = """
            {
              "manifestVersion": "ksmt-ml-dataset-v1",
              "status": "complete",
              "datasetId": ${json(datasetId)},
              "featureSchema": "$KSMT_MACHSMT_INSPIRED_SCHEMA_VERSION",
              "featureCount": $KSMT_MACHSMT_INSPIRED_FEATURE_COUNT,
              "inputRootName": ${json(inputRoot.fileName.toString())},
              "inputManifest": ${json(inputManifestPath.fileName.toString())},
              "inputManifestSha256": "$inputManifestSha256",
              "outputFile": ${json(outputPath.fileName.toString())},
              "outputSha256": "$outputSha256",
              "schemaFile": ${json(schemaPath.fileName.toString())},
              "schemaSha256": "$schemaSha256",
              "codeRevision": ${json(codeRevision)},
              "ksmtVersion": "0.6.7",
              "javaVersion": ${json(System.getProperty("java.version"))},
              "generatedAtUtc": ${json(Instant.now().toString())},
              "counts": {
                "total": $total,
                "successful": $successful,
                "partial": $partial,
                "failed": $failed
              }
            }
        """.trimIndent() + "\n"
        Files.writeString(path, json, StandardCharsets.UTF_8)
    }

    private fun sha256(path: Path): String = sha256(Files.readAllBytes(path))

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256")
            .digest(bytes)
            .joinToString("") { byte -> "%02x".format(byte) }

    private fun json(value: String): String =
        "\"${value.replace("\\", "\\\\").replace("\"", "\\\"")}\""

    private fun compactError(error: Exception): String =
        "${error::class.simpleName}: ${error.message}".replace(Regex("\\s+"), " ").take(500)

    private fun csv(value: String): String =
        if (value.any { it == ',' || it == '"' || it == '\n' || it == '\r' }) {
            "\"${value.replace("\"", "\"\"")}\""
        } else {
            value
        }

    private fun defaultWorkers(): Int =
        Runtime.getRuntime().availableProcessors().coerceIn(1, 4)

    private const val FEATURE_COUNT = KSMT_MACHSMT_INSPIRED_FEATURE_COUNT
    private val STRUCTURAL_FEATURE_INDICES = 145..155
    private val METADATA_COLUMNS = listOf(
        "dataset_id",
        "feature_schema",
        "benchmark_sha256",
        "relative_path",
        "logic",
        "family",
        "expected_status",
        "extraction_status",
        "error",
    )
}

private data class DatasetFeatureRow(
    val relativePath: String,
    val benchmarkSha256: String,
    val logic: String,
    val family: String,
    val expectedStatus: String,
    val status: String,
    val error: String,
    val values: List<Double>,
)

private data class IndexedDatasetFeatureRow(
    val index: Int,
    val row: DatasetFeatureRow,
)
