package io.ksmt.ml.onnx

import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.HexFormat

/** Infrastructure-facing entry point for a complete cvc5 ONNX policy bundle. */
class Cvc5OnnxPolicy(bundleDirectory: Path) : AutoCloseable {
    private val decoder = Cvc5OptionDecoder.fromManifest(bundleDirectory.resolve(MANIFEST_FILE))
    private val modelPath = bundleDirectory.resolve(decoder.modelFile)
    private val model: OnnxPolicyModel

    val bundleId: String
        get() = decoder.bundleId

    val featureSchema: String
        get() = decoder.featureSchema

    val solverVersion: String
        get() = decoder.solverVersion

    init {
        require(Files.isRegularFile(modelPath)) { "ONNX model does not exist: $modelPath" }
        val actualSha256 = sha256(modelPath)
        require(actualSha256 == decoder.modelSha256) {
            "ONNX model checksum mismatch: expected ${decoder.modelSha256}, got $actualSha256"
        }
        require(decoder.featureSchema == OnnxPolicyModel.FEATURE_SCHEMA) {
            "Bundle feature schema '${decoder.featureSchema}' is not supported"
        }
        require(decoder.parameterCount == OnnxPolicyModel.PARAMETER_COUNT) {
            "Bundle defines ${decoder.parameterCount} parameters, expected " +
                OnnxPolicyModel.PARAMETER_COUNT
        }
        model = OnnxPolicyModel(modelPath)
    }

    fun predict(rows: List<DoubleArray>): List<Cvc5SolverConfiguration> =
        model.predict(featureSchema, rows).map(decoder::decode)

    override fun close() {
        model.close()
    }

    companion object {
        private const val MANIFEST_FILE = "manifest.json"

        private fun sha256(path: Path): String {
            val digest = MessageDigest.getInstance("SHA-256")
            Files.newInputStream(path).use { input ->
                val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                }
            }
            return HexFormat.of().formatHex(digest.digest())
        }
    }
}
