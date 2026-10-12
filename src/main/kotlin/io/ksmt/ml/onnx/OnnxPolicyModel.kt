package io.ksmt.ml.onnx

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.nio.FloatBuffer
import java.nio.file.Path

data class PolicyPrediction(
    val actionId: Int,
    val actionScores: FloatArray,
    val configValues: LongArray,
    val configMask: BooleanArray,
)

class OnnxPolicyModel(modelPath: Path) : AutoCloseable {
    private val environment = OrtEnvironment.getEnvironment()
    private val sessionOptions = OrtSession.SessionOptions()
    private val session = environment.createSession(modelPath.toString(), sessionOptions)

    init {
        require(session.inputNames == setOf(INPUT_NAME)) {
            "Unexpected ONNX inputs: ${session.inputNames}"
        }
        require(session.outputNames == OUTPUT_NAMES) {
            "Unexpected ONNX outputs: ${session.outputNames}"
        }
    }

    fun predict(
        featureSchema: String,
        rows: List<DoubleArray>,
    ): List<PolicyPrediction> {
        require(featureSchema == FEATURE_SCHEMA) {
            "Expected feature schema '$FEATURE_SCHEMA', got '$featureSchema'"
        }
        require(rows.isNotEmpty()) { "Inference batch must not be empty" }

        val flat = FloatArray(rows.size * FEATURE_COUNT)
        rows.forEachIndexed { rowIndex, row ->
            require(row.size == FEATURE_COUNT) {
                "Row $rowIndex has ${row.size} features, expected $FEATURE_COUNT"
            }
            row.forEachIndexed { featureIndex, value ->
                require(value.isFinite() && value >= 0.0) {
                    "Row $rowIndex feature ${featureIndex + 1} must be finite and non-negative"
                }
                val floatValue = value.toFloat()
                require(floatValue.isFinite()) {
                    "Row $rowIndex feature ${featureIndex + 1} is outside float32 range"
                }
                flat[rowIndex * FEATURE_COUNT + featureIndex] = floatValue
            }
        }

        OnnxTensor.createTensor(
            environment,
            FloatBuffer.wrap(flat),
            longArrayOf(rows.size.toLong(), FEATURE_COUNT.toLong()),
        ).use { input ->
            session.run(mapOf(INPUT_NAME to input)).use { result ->
                @Suppress("UNCHECKED_CAST")
                val scores = result.required(ACTION_SCORES).value as Array<FloatArray>
                val actionIds = result.required(ACTION_ID).value as LongArray
                @Suppress("UNCHECKED_CAST")
                val configValues = result.required(CONFIG_VALUES).value as Array<LongArray>
                @Suppress("UNCHECKED_CAST")
                val configMasks = result.required(CONFIG_MASK).value as Array<BooleanArray>

                require(
                    scores.size == rows.size &&
                        actionIds.size == rows.size &&
                        configValues.size == rows.size &&
                        configMasks.size == rows.size
                ) {
                    "ONNX output batch dimensions do not match input batch ${rows.size}"
                }

                return rows.indices.map { index ->
                    require(scores[index].size == ACTION_COUNT) {
                        "ONNX action_scores row $index has ${scores[index].size} values, expected $ACTION_COUNT"
                    }
                    require(actionIds[index] in 0 until ACTION_COUNT.toLong()) {
                        "ONNX action_id row $index is outside the action catalog: ${actionIds[index]}"
                    }
                    require(configValues[index].size == PARAMETER_COUNT) {
                        "ONNX config_values row $index has ${configValues[index].size} values, expected $PARAMETER_COUNT"
                    }
                    require(configMasks[index].size == PARAMETER_COUNT) {
                        "ONNX config_mask row $index has ${configMasks[index].size} values, expected $PARAMETER_COUNT"
                    }
                    PolicyPrediction(
                        actionId = actionIds[index].toInt(),
                        actionScores = scores[index].copyOf(),
                        configValues = configValues[index].copyOf(),
                        configMask = configMasks[index].copyOf(),
                    )
                }
            }
        }
    }

    override fun close() {
        session.close()
        sessionOptions.close()
    }

    private fun OrtSession.Result.required(name: String) =
        get(name).orElseThrow { IllegalStateException("ONNX output '$name' is missing") }

    companion object {
        const val FEATURE_SCHEMA = "ksmt-machsmt-inspired-v1"
        const val FEATURE_COUNT = 157
        const val ACTION_COUNT = 4
        const val PARAMETER_COUNT = 30

        const val INPUT_NAME = "features"
        const val ACTION_SCORES = "action_scores"
        const val ACTION_ID = "action_id"
        const val CONFIG_VALUES = "config_values"
        const val CONFIG_MASK = "config_mask"

        val OUTPUT_NAMES = setOf(ACTION_SCORES, ACTION_ID, CONFIG_VALUES, CONFIG_MASK)
    }
}
