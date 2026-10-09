package io.ksmt.ml.features

import org.junit.jupiter.api.DynamicTest
import org.junit.jupiter.api.TestFactory
import kotlin.test.Test
import kotlin.test.assertEquals

class MachSmtGrammarFeatureTest {
    @TestFactory
    fun `every defined grammar feature is wired to its SMT-LIB token`(): List<DynamicTest> =
        MachSmtFeatureSchema.definitions
            .filter { it.id in 1..145 }
            .map { definition ->
                DynamicTest.dynamicTest("feature ${definition.id}: ${definition.token}") {
                    val result = MachSmtFeatureExtractor().extract(
                        KsmtAstFeatureInput(
                            assertions = emptyList(),
                            syntaxTokenCounts = mapOf(requireNotNull(definition.token) to 7L),
                        )
                    )

                    val feature = result.feature(definition.id)
                    assertEquals(7.0, feature.value)
                    assertEquals("smtlib-token", feature.source)
                    assertEquals(definition.cluster.jsonName, feature.cluster)
                }
            }

    @TestFactory
    fun `scanner recognizes every defined grammar token`(): List<DynamicTest> =
        MachSmtFeatureSchema.definitions
            .filter { it.id in 1..145 }
            .map { definition ->
                DynamicTest.dynamicTest("scanner token ${definition.id}: ${definition.token}") {
                    val token = requireNotNull(definition.token)
                    val counts = SmtLibSyntaxScanner.countTokens("($token)")
                    assertEquals(1L, counts[token])
                }
            }

    @Test
    fun `source size is feature 157`() {
        val result = MachSmtFeatureExtractor().extract(
            KsmtAstFeatureInput(
                assertions = emptyList(),
                syntaxTokenCounts = emptyMap(),
                sourceSizeBytes = 1234,
            )
        )

        val feature = result.feature(157)
        assertEquals(1234.0, feature.value)
        assertEquals("source-metadata", feature.source)
        assertEquals(FeatureCluster.SOURCE_METADATA.jsonName, feature.cluster)
    }

    private fun MachSmtFeatureResult.feature(id: Int): MachSmtFeatureValue =
        features.single { it.id == id }
}
