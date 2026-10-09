package io.ksmt.ml.dataset

import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.readLines
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

class DatasetFeatureExtractorTest {
    @TempDir
    lateinit var temporaryDirectory: Path

    @Test
    fun `parallel extraction preserves deterministic path order`() {
        val dataset = temporaryDirectory.resolve("dataset")
        writeBenchmark(dataset, "QF_LIA/family/c.smt2", 3)
        writeBenchmark(dataset, "QF_LIA/family/a.smt2", 1)
        writeBenchmark(dataset, "QF_LIA/family/b.smt2", 2)

        val firstOutput = temporaryDirectory.resolve("first.csv")
        val secondOutput = temporaryDirectory.resolve("second.csv")
        val inputManifest = temporaryDirectory.resolve("input-manifest.tsv")
        Files.writeString(
            inputManifest,
            "dataset_id\tlogic\tbenchmarks\nfixture-dataset-v1\tQF_LIA\t3\n",
        )

        val first = DatasetFeatureExtractor.extract(
            inputRoot = dataset,
            outputPath = firstOutput,
            datasetId = "fixture-dataset-v1",
            inputManifestPath = inputManifest,
            codeRevision = "test-revision",
            workers = 2,
            progressEvery = 0,
        )
        val second = DatasetFeatureExtractor.extract(
            inputRoot = dataset,
            outputPath = secondOutput,
            datasetId = "fixture-dataset-v1",
            inputManifestPath = inputManifest,
            codeRevision = "test-revision",
            workers = 3,
            progressEvery = 0,
        )

        assertEquals(3, first.total)
        assertEquals(3, first.successful)
        assertEquals(0, first.partial)
        assertEquals(0, first.failed)
        assertEquals(first.total, second.total)
        assertEquals(first.successful, second.successful)
        assertEquals(firstOutput.readLines(), secondOutput.readLines())

        val lines = firstOutput.readLines()
        val header = lines.first().split(',')
        assertEquals("dataset_id", header[0])
        assertEquals("feature_schema", header[1])
        assertEquals("benchmark_sha256", header[2])
        assertEquals(166, header.size)

        val paths = lines.drop(1).map { line -> line.split(',')[3] }
        assertEquals(
            listOf(
                "QF_LIA/family/a.smt2",
                "QF_LIA/family/b.smt2",
                "QF_LIA/family/c.smt2",
            ),
            paths,
        )
        lines.drop(1).forEach { line ->
            val fields = line.split(',')
            assertEquals("fixture-dataset-v1", fields[0])
            assertEquals("ksmt-machsmt-inspired-v1", fields[1])
            assertTrue(fields[2].matches(Regex("[0-9a-f]{64}")))
            assertEquals("sat", fields[6])
            assertEquals("ok", fields[7])
        }
        assertTrue(Files.isRegularFile(first.schemaPath))
        assertTrue(Files.isRegularFile(first.manifestPath))
        val manifest = Files.readString(first.manifestPath)
        assertTrue(manifest.contains("\"status\": \"complete\""))
        assertTrue(manifest.contains("\"datasetId\": \"fixture-dataset-v1\""))
        assertTrue(manifest.contains("\"featureCount\": 157"))
        assertTrue(manifest.contains("\"codeRevision\": \"test-revision\""))
    }

    @Test
    fun `rejects paths that do not identify logic and family`() {
        val dataset = temporaryDirectory.resolve("invalid-dataset")
        writeBenchmark(dataset, "orphan.smt2", 1)
        val inputManifest = temporaryDirectory.resolve("invalid-input-manifest.tsv")
        Files.writeString(
            inputManifest,
            "dataset_id\tlogic\tbenchmarks\ninvalid-fixture\tUNKNOWN\t1\n",
        )

        assertFailsWith<IllegalArgumentException> {
            DatasetFeatureExtractor.extract(
                inputRoot = dataset,
                outputPath = temporaryDirectory.resolve("invalid.csv"),
                datasetId = "invalid-fixture",
                inputManifestPath = inputManifest,
                codeRevision = "test-revision",
                progressEvery = 0,
            )
        }
    }

    @Test
    fun `rejects a manifest for another dataset identity`() {
        val dataset = temporaryDirectory.resolve("dataset-id-mismatch")
        writeBenchmark(dataset, "QF_LIA/family/a.smt2", 1)
        val inputManifest = temporaryDirectory.resolve("mismatched-input-manifest.tsv")
        Files.writeString(
            inputManifest,
            "dataset_id\tlogic\tbenchmarks\nother-dataset\tQF_LIA\t1\n",
        )

        assertFailsWith<IllegalArgumentException> {
            DatasetFeatureExtractor.extract(
                inputRoot = dataset,
                outputPath = temporaryDirectory.resolve("mismatched.csv"),
                datasetId = "expected-dataset",
                inputManifestPath = inputManifest,
                codeRevision = "test-revision",
                progressEvery = 0,
            )
        }
    }

    private fun writeBenchmark(root: Path, relativePath: String, lowerBound: Int) {
        val path = root.resolve(relativePath)
        Files.createDirectories(path.parent)
        Files.writeString(
            path,
            """
                (set-logic QF_LIA)
                (set-info :status sat)
                (declare-const x Int)
                (assert (> x $lowerBound))
                (check-sat)
            """.trimIndent(),
        )
    }
}
