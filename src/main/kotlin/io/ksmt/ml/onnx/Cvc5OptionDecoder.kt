package io.ksmt.ml.onnx

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import java.nio.file.Path

data class Cvc5SolverConfiguration(
    val actionId: Int,
    val actionName: String,
    val options: Map<String, String>,
)

/**
 * Decodes the fixed-width numeric ONNX output into cvc5 option strings.
 *
 * The manifest is the single source of truth for parameter order and value
 * encodings. Callers must not duplicate this mapping in infrastructure code.
 */
class Cvc5OptionDecoder private constructor(
    val bundleId: String,
    val featureSchema: String,
    val solverVersion: String,
    val modelFile: String,
    val modelSha256: String,
    private val parameters: List<Parameter>,
    private val actions: Map<Int, Action>,
) {
    val parameterCount: Int
        get() = parameters.size

    fun decode(prediction: PolicyPrediction): Cvc5SolverConfiguration {
        require(prediction.configValues.size == parameterCount) {
            "config_values has ${prediction.configValues.size} entries, expected $parameterCount"
        }
        require(prediction.configMask.size == parameterCount) {
            "config_mask has ${prediction.configMask.size} entries, expected $parameterCount"
        }

        val options = linkedMapOf<String, String>()
        parameters.forEach { parameter ->
            if (prediction.configMask[parameter.index]) {
                val encodedValue = prediction.configValues[parameter.index]
                val decodedValue = parameter.valuesByCode[encodedValue]
                    ?: throw IllegalArgumentException(
                        "Unknown encoded value $encodedValue for cvc5 option '${parameter.name}'"
                    )
                options[parameter.name] = decodedValue
            }
        }

        val action = actions[prediction.actionId]
            ?: throw IllegalArgumentException(
                "Unknown action_id ${prediction.actionId} for bundle '$bundleId'"
            )
        require(options == action.options) {
            "Decoded options $options do not match action_id ${prediction.actionId} " +
                "('${action.name}'): ${action.options}"
        }

        return Cvc5SolverConfiguration(
            actionId = prediction.actionId,
            actionName = action.name,
            options = options.toMap(),
        )
    }

    private data class Parameter(
        val index: Int,
        val name: String,
        val valuesByCode: Map<Long, String>,
    )

    private data class Action(
        val name: String,
        val options: Map<String, String>,
    )

    companion object {
        private const val BUNDLE_FORMAT = "ksmt-ml-onnx-bundle-v1"
        private val objectMapper = ObjectMapper()

        fun fromManifest(path: Path): Cvc5OptionDecoder {
            val root = objectMapper.readTree(path.toFile())
            require(root.requiredText("bundleFormat") == BUNDLE_FORMAT) {
                "Unsupported ONNX bundle format: ${root.path("bundleFormat").asText()}"
            }

            val parameterNodes = root.requiredArray("parameters")
            val parameters = parameterNodes.map { node ->
                val index = node.requiredInt("index")
                val name = node.requiredText("name")
                val encoding = node.requiredObject("encoding")
                val valuesByCode = linkedMapOf<Long, String>()
                encoding.fields().forEach { (value, codeNode) ->
                    require(codeNode.isIntegralNumber) {
                        "Encoding for cvc5 option '$name' and value '$value' must be an integer"
                    }
                    val previous = valuesByCode.put(codeNode.asLong(), value)
                    require(previous == null) {
                        "Duplicate encoding ${codeNode.asLong()} for cvc5 option '$name'"
                    }
                }
                require(valuesByCode.isNotEmpty()) {
                    "cvc5 option '$name' has an empty encoding"
                }
                require(node.requiredText("default") in valuesByCode.values) {
                    "Default value for cvc5 option '$name' is absent from its encoding"
                }
                Parameter(index = index, name = name, valuesByCode = valuesByCode)
            }.sortedBy { it.index }

            require(parameters.map { it.index } == parameters.indices.toList()) {
                "Parameter indices must be unique and contiguous from zero"
            }
            require(parameters.map { it.name }.distinct().size == parameters.size) {
                "Parameter names must be unique"
            }
            val parametersByName = parameters.associateBy { it.name }

            val actionSpace = root.requiredObject("actionSpace")
            val actions = linkedMapOf<Int, Action>()
            actionSpace.requiredArray("actions").forEach { node ->
                val id = node.requiredInt("id")
                val name = node.requiredText("name")
                val options = linkedMapOf<String, String>()
                node.requiredObject("options").fields().forEach { (optionName, valueNode) ->
                    require(valueNode.isTextual) {
                        "Value for cvc5 option '$optionName' in action $id must be a string"
                    }
                    val value = valueNode.asText()
                    val parameter = parametersByName[optionName]
                        ?: throw IllegalArgumentException(
                            "Action $id references unknown cvc5 option '$optionName'"
                        )
                    require(value in parameter.valuesByCode.values) {
                        "Action $id uses unknown value '$value' for cvc5 option '$optionName'"
                    }
                    options[optionName] = value
                }
                require(actions.put(id, Action(name, options.toMap())) == null) {
                    "Duplicate action id $id"
                }
            }

            require(actions.isNotEmpty()) { "Action catalog must not be empty" }

            return Cvc5OptionDecoder(
                bundleId = root.requiredText("bundleId"),
                featureSchema = root.requiredObject("input").requiredText("featureSchema"),
                solverVersion = actionSpace.requiredText("solverVersion"),
                modelFile = root.requiredObject("model").requiredText("file").also { file ->
                    require(Path.of(file).fileName.toString() == file) {
                        "Manifest model file must be a plain filename"
                    }
                },
                modelSha256 = root.requiredObject("model").requiredText("sha256").also { sha256 ->
                    require(sha256.matches(Regex("[0-9a-f]{64}"))) {
                        "Manifest model sha256 must contain 64 lowercase hexadecimal characters"
                    }
                },
                parameters = parameters,
                actions = actions,
            )
        }

        private fun JsonNode.requiredText(field: String): String {
            val value = get(field)
            require(value != null && value.isTextual && value.asText().isNotEmpty()) {
                "Manifest field '$field' must be a non-empty string"
            }
            return value.asText()
        }

        private fun JsonNode.requiredInt(field: String): Int {
            val value = get(field)
            require(value != null && value.isIntegralNumber && value.canConvertToInt()) {
                "Manifest field '$field' must be an integer"
            }
            return value.asInt()
        }

        private fun JsonNode.requiredArray(field: String): List<JsonNode> {
            val value = get(field)
            require(value != null && value.isArray) {
                "Manifest field '$field' must be an array"
            }
            return value.toList()
        }

        private fun JsonNode.requiredObject(field: String): JsonNode {
            val value = get(field)
            require(value != null && value.isObject) {
                "Manifest field '$field' must be an object"
            }
            return value
        }
    }
}
