package io.ksmt.ml.onnx

import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class OnnxPolicyModelTest {
    @Test
    fun `runs a dynamic batch and returns encoded configurations`() {
        val rows = listOf(
            row(),
            row(47 to 3.0),
            row(133 to 10.0),
            row(33 to 2.0),
        )

        OnnxPolicyModel(MODEL_PATH).use { model ->
            val predictions = model.predict(OnnxPolicyModel.FEATURE_SCHEMA, rows)
            val decoder = Cvc5OptionDecoder.fromManifest(MANIFEST_PATH)
            val configurations = predictions.map(decoder::decode)

            assertEquals(listOf(0, 1, 2, 3), predictions.map { it.actionId })
            predictions.forEach {
                assertEquals(OnnxPolicyModel.ACTION_COUNT, it.actionScores.size)
                assertEquals(OnnxPolicyModel.PARAMETER_COUNT, it.configValues.size)
                assertEquals(OnnxPolicyModel.PARAMETER_COUNT, it.configMask.size)
            }
            assertEquals(OnnxPolicyModel.PARAMETER_COUNT, decoder.parameterCount)
            assertEquals(
                listOf(
                    emptyMap(),
                    mapOf("arrays-eager-lemmas" to "true"),
                    mapOf("arith-prop" to "unate"),
                    mapOf("decision" to "justification"),
                ),
                configurations.map { it.options },
            )
            assertEquals(
                listOf(
                    "default",
                    "arrays-eager-lemmas",
                    "arith-prop-unate",
                    "decision-justification",
                ),
                configurations.map { it.actionName },
            )
        }
    }

    @Test
    fun `decoder rejects a value outside the manifest encoding`() {
        val decoder = Cvc5OptionDecoder.fromManifest(MANIFEST_PATH)
        val invalid = PolicyPrediction(
            actionId = 1,
            actionScores = FloatArray(OnnxPolicyModel.ACTION_COUNT),
            configValues = LongArray(OnnxPolicyModel.PARAMETER_COUNT).also { it[14] = 99 },
            configMask = BooleanArray(OnnxPolicyModel.PARAMETER_COUNT).also { it[14] = true },
        )

        assertFailsWith<IllegalArgumentException> { decoder.decode(invalid) }
    }

    @Test
    fun `bundle entry point returns ready to apply cvc5 options`() {
        Cvc5OnnxPolicy(BUNDLE_PATH).use { policy ->
            val configurations = policy.predict(listOf(row(133 to 10.0), row()))

            assertEquals("cvc5-policy-prototype-v1", policy.bundleId)
            assertEquals("ksmt-machsmt-inspired-v1", policy.featureSchema)
            assertEquals("1.3.4", policy.solverVersion)
            assertEquals(mapOf("arith-prop" to "unate"), configurations[0].options)
            assertEquals(emptyMap(), configurations[1].options)
        }
    }

    @Test
    fun `rejects a mismatched feature schema`() {
        OnnxPolicyModel(MODEL_PATH).use { model ->
            assertFailsWith<IllegalArgumentException> {
                model.predict("some-other-schema", listOf(row()))
            }
        }
    }

    @Test
    fun `rejects partial and malformed feature rows`() {
        OnnxPolicyModel(MODEL_PATH).use { model ->
            assertFailsWith<IllegalArgumentException> {
                model.predict(OnnxPolicyModel.FEATURE_SCHEMA, listOf(DoubleArray(156)))
            }
            assertFailsWith<IllegalArgumentException> {
                model.predict(
                    OnnxPolicyModel.FEATURE_SCHEMA,
                    listOf(row().also { it[145] = Double.NaN }),
                )
            }
            assertFailsWith<IllegalArgumentException> {
                model.predict(
                    OnnxPolicyModel.FEATURE_SCHEMA,
                    listOf(row().also { it[145] = Double.MAX_VALUE }),
                )
            }
        }
    }

    private fun row(vararg values: Pair<Int, Double>): DoubleArray =
        DoubleArray(OnnxPolicyModel.FEATURE_COUNT).also { row ->
            values.forEach { (featureId, value) -> row[featureId - 1] = value }
        }

    companion object {
        private val BUNDLE_PATH = Path.of("model-bundles", "cvc5-policy-prototype-v1")
        private val MODEL_PATH = BUNDLE_PATH.resolve("model.onnx")
        private val MANIFEST_PATH = BUNDLE_PATH.resolve("manifest.json")
    }
}
