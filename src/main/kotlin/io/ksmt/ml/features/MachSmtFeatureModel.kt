package io.ksmt.ml.features

import io.ksmt.expr.KExpr
import io.ksmt.sort.KBoolSort

const val KSMT_MACHSMT_INSPIRED_SCHEMA_VERSION = "ksmt-machsmt-inspired-v1"
const val KSMT_MACHSMT_INSPIRED_FEATURE_COUNT = 157
const val UNAVAILABLE_FEATURE_VALUE = -1.0

enum class FeatureCluster(val jsonName: String) {
    PROBLEM_COMMANDS("problem-commands"),
    DECLARATIONS("declarations"),
    CONTROL_COMMANDS("control-commands"),
    QUANTIFIERS_AND_BINDINGS("quantifiers-and-bindings"),
    BOOLEAN_CORE("boolean-core"),
    ARRAYS("arrays"),
    BIT_VECTORS("bit-vectors"),
    FLOATING_POINT("floating-point"),
    ARITHMETIC("arithmetic"),
    UNINTERPRETED_FUNCTIONS("uninterpreted-functions"),
    SOURCE_METADATA("source-metadata"),
}

data class KsmtAstFeatureInput(
    val assertions: List<KExpr<KBoolSort>>,
    val syntaxTokenCounts: Map<String, Long>? = null,
    val sourceSizeBytes: Long? = null,
    val sourceName: String? = null,
)

data class MachSmtFeatureValue(
    val id: Int,
    val name: String,
    val cluster: String,
    val value: Double,
    val source: String,
)

data class MachSmtFeatureResult(
    val schemaVersion: String,
    val sourceName: String? = null,
    val featureCount: Int,
    val vector: List<Double>,
    val features: List<MachSmtFeatureValue>,
    val unavailableFeatureIds: List<Int>,
    val notes: List<String>,
)

internal data class FeatureDefinition(
    val id: Int,
    val name: String,
    val cluster: FeatureCluster,
    val token: String? = null,
)
