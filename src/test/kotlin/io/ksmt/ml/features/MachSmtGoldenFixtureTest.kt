package io.ksmt.ml.features

import io.ksmt.KContext
import io.ksmt.KContext.SimplificationMode.NO_SIMPLIFY
import io.ksmt.solver.z3.KZ3SMTLibParser
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import org.junit.jupiter.api.io.TempDir

class MachSmtGoldenFixtureTest {
    @TempDir
    lateinit var temporaryDirectory: Path

    @Test
    fun `published golden expectations hold across theory families`() {
        val expectations = resourceText("expected.tsv")
            .lineSequence()
            .drop(1)
            .filter(String::isNotBlank)
            .map { line ->
                val fields = line.split('\t')
                GoldenExpectation(
                    fixture = fields[0],
                    featureId = fields[1].toInt(),
                    expectedValue = fields[2].toDouble(),
                    reason = fields[3],
                )
            }
            .groupBy(GoldenExpectation::fixture)

        assertEquals(
            setOf(
                "boolean.smt2",
                "arrays.smt2",
                "bitvectors.smt2",
                "arithmetic.smt2",
                "quantifiers.smt2",
                "uf.smt2",
                "floating-point.smt2",
            ),
            expectations.keys,
        )

        expectations.forEach { (fixture, expectedFeatures) ->
            val source = resourceText(fixture)
            val path = temporaryDirectory.resolve(fixture)
            Files.writeString(path, source)

            val result = KContext(simplificationMode = NO_SIMPLIFY).use { context ->
                MachSmtFeatureExtractor().extract(
                    KsmtAstFeatureInput(
                        assertions = KZ3SMTLibParser(context).parse(path),
                        syntaxTokenCounts = SmtLibSyntaxScanner.countTokens(source),
                        sourceSizeBytes = source.toByteArray().size.toLong(),
                        sourceName = fixture,
                    )
                )
            }

            assertEquals(157, result.featureCount, fixture)
            expectedFeatures.forEach { expected ->
                assertEquals(
                    expected.expectedValue,
                    result.features.single { it.id == expected.featureId }.value,
                    1e-12,
                    "$fixture feature ${expected.featureId}: ${expected.reason}",
                )
            }
        }
    }

    private fun resourceText(name: String): String =
        requireNotNull(javaClass.getResourceAsStream("/fixtures/features/$name")) {
            "Missing golden fixture resource: $name"
        }.bufferedReader().use { it.readText() }

    private data class GoldenExpectation(
        val fixture: String,
        val featureId: Int,
        val expectedValue: Double,
        val reason: String,
    )
}
