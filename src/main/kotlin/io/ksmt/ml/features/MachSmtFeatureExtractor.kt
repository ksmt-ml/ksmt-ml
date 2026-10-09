package io.ksmt.ml.features

import io.ksmt.decl.KUninterpretedFuncDecl
import io.ksmt.expr.KApp
import io.ksmt.expr.KArraySelectBase
import io.ksmt.expr.KArrayStoreBase
import io.ksmt.expr.KBvAddExpr
import io.ksmt.expr.KExistentialQuantifier
import io.ksmt.expr.KExpr
import io.ksmt.expr.KFunctionApp
import io.ksmt.expr.KQuantifier
import io.ksmt.expr.KUniversalQuantifier
import kotlin.math.pow
import kotlin.math.sqrt

class MachSmtFeatureExtractor(
    private val maxAstNodeVisits: Int = DEFAULT_MAX_AST_NODE_VISITS,
) {
    init {
        require(maxAstNodeVisits > 0) { "maxAstNodeVisits must be positive" }
    }

    fun extract(input: KsmtAstFeatureInput): MachSmtFeatureResult {
        val statistics = collectAstStatistics(input.assertions)
        val values = ArrayList<MachSmtFeatureValue>(KSMT_MACHSMT_INSPIRED_FEATURE_COUNT)

        for (definition in MachSmtFeatureSchema.definitions) {
            values += when (definition.id) {
                in 1..145 -> grammarFeature(definition, input.syntaxTokenCounts, statistics.operatorCounts)
                146 -> derivedFeature(definition, statistics.selectsPerArray.values.averageOrZero())
                147 -> derivedFeature(definition, statistics.storeChainDepths.averageOrZero())
                148 -> derivedFeature(definition, statistics.bvAdderChainSizes.averageOrZero())
                149 -> derivedFeature(definition, statistics.bvAdderChainSizes.medianOrZero())
                150 -> derivedFeature(definition, statistics.bvAdderChainSizes.populationDeviationOrZero())
                151 -> derivedFeature(definition, statistics.forallBoundVariables.toDouble())
                152 -> derivedFeature(definition, statistics.existsBoundVariables.toDouble())
                153 -> derivedFeature(
                    definition,
                    if (statistics.forallBoundVariables == 0L) {
                        0.0
                    } else {
                        statistics.existsBoundVariables.toDouble() / statistics.forallBoundVariables
                    },
                )
                154 -> derivedFeature(definition, statistics.quantifierNestingLevels.averageOrZero())
                155 -> derivedFeature(definition, statistics.ufArities.averageOrZero())
                156 -> derivedFeature(definition, statistics.ufApplications.values.averageOrZero())
                157 -> if (input.sourceSizeBytes == null) {
                    unavailableFeature(definition)
                } else {
                    MachSmtFeatureValue(
                        id = definition.id,
                        name = definition.name,
                        cluster = definition.cluster.jsonName,
                        value = input.sourceSizeBytes.toDouble(),
                        source = "source-metadata",
                    )
                }
                else -> error("Unexpected feature id: ${definition.id}")
            }
        }

        val unavailable = values.filter { it.source == "unavailable" }.map { it.id }
        val notes = buildList {
            if (input.syntaxTokenCounts == null) {
                add("SMT-LIB syntax summary is absent; command, declaration, let, and sort-token features are unavailable.")
            }
            if (input.sourceSizeBytes == null) {
                add("Source size is unavailable for an AST-only input.")
            }
        }

        return MachSmtFeatureResult(
            schemaVersion = KSMT_MACHSMT_INSPIRED_SCHEMA_VERSION,
            sourceName = input.sourceName,
            featureCount = values.size,
            vector = values.map { it.value },
            features = values,
            unavailableFeatureIds = unavailable,
            notes = notes,
        )
    }

    private fun grammarFeature(
        definition: FeatureDefinition,
        syntaxTokenCounts: Map<String, Long>?,
        astOperatorCounts: Map<String, Long>,
    ): MachSmtFeatureValue {
        val token = definition.token ?: return unavailableFeature(definition)

        if (syntaxTokenCounts != null) {
            return MachSmtFeatureValue(
                id = definition.id,
                name = definition.name,
                cluster = definition.cluster.jsonName,
                value = syntaxTokenCounts.getOrDefault(token, 0L).toDouble(),
                source = "smtlib-token",
            )
        }

        if (token !in astCountableTokens) {
            return unavailableFeature(definition)
        }

        return MachSmtFeatureValue(
            id = definition.id,
            name = definition.name,
            cluster = definition.cluster.jsonName,
            value = astOperatorCounts.getOrDefault(token, 0L).toDouble(),
            source = "ksmt-ast",
        )
    }

    private fun derivedFeature(definition: FeatureDefinition, value: Double) =
        MachSmtFeatureValue(
            id = definition.id,
            name = definition.name,
            cluster = definition.cluster.jsonName,
            value = value,
            source = "ksmt-ast-derived",
        )

    private fun unavailableFeature(definition: FeatureDefinition) =
        MachSmtFeatureValue(
            id = definition.id,
            name = definition.name,
            cluster = definition.cluster.jsonName,
            value = UNAVAILABLE_FEATURE_VALUE,
            source = "unavailable",
        )

    private fun collectAstStatistics(assertions: List<KExpr<*>>): AstStatistics {
        val statistics = AstStatistics()
        val stack = ArrayDeque<VisitEntry>()
        assertions.asReversed().forEach { stack.addLast(VisitEntry(it, quantifierDepth = 0)) }
        var visitedNodes = 0

        while (stack.isNotEmpty()) {
            visitedNodes++
            check(visitedNodes <= maxAstNodeVisits) {
                "AST traversal exceeded $maxAstNodeVisits node visits; " +
                    "the expression DAG is too expensive to expand as a tree"
            }
            val entry = stack.removeLast()
            val expression = entry.expression

            when (expression) {
                is KUniversalQuantifier -> {
                    statistics.incrementOperator("forall")
                    statistics.forallBoundVariables += expression.bounds.size
                    val depth = entry.quantifierDepth + 1
                    statistics.quantifierNestingLevels += depth
                    stack += VisitEntry(expression.body, quantifierDepth = depth)
                }
                is KExistentialQuantifier -> {
                    statistics.incrementOperator("exists")
                    statistics.existsBoundVariables += expression.bounds.size
                    val depth = entry.quantifierDepth + 1
                    statistics.quantifierNestingLevels += depth
                    stack += VisitEntry(expression.body, quantifierDepth = depth)
                }
                is KApp<*, *> -> {
                    statistics.incrementOperator(expression.decl.name.toSmtLibToken())

                    if (expression is KFunctionApp<*> &&
                        expression.decl is KUninterpretedFuncDecl<*> &&
                        expression.args.isNotEmpty()
                    ) {
                        statistics.ufAritiesByDecl.putIfAbsent(expression.decl, expression.args.size)
                        statistics.ufApplications[expression.decl] =
                            statistics.ufApplications.getOrDefault(expression.decl, 0L) + 1L
                    }

                    if (expression is KArraySelectBase<*, *> ) {
                        statistics.selectsPerArray[expression.array] =
                            statistics.selectsPerArray.getOrDefault(expression.array, 0L) + 1L
                    }

                    if (expression is KArrayStoreBase<*, *> && !entry.parentIsStoreArray) {
                        val depth = storeChainDepth(expression)
                        if (depth > 1) statistics.storeChainDepths += depth
                    }

                    if (expression is KBvAddExpr<*> && !entry.parentIsBvAdd) {
                        val size = bvAdderChainSize(expression)
                        if (size > 1) statistics.bvAdderChainSizes += size
                    }

                    expression.args.asReversed().forEachIndexed { reversedIndex, argument ->
                        val originalIndex = expression.args.lastIndex - reversedIndex
                        stack += VisitEntry(
                            expression = argument,
                            quantifierDepth = entry.quantifierDepth,
                            parentIsStoreArray = expression is KArrayStoreBase<*, *> && originalIndex == 0,
                            parentIsBvAdd = expression is KBvAddExpr<*>,
                        )
                    }
                }
                is KQuantifier -> error("Unknown KSMT quantifier type: ${expression::class.qualifiedName}")
            }
        }

        statistics.ufArities += statistics.ufAritiesByDecl.values
        return statistics
    }

    private fun storeChainDepth(store: KArrayStoreBase<*, *>): Int {
        var depth = 0
        var current: KExpr<*> = store
        while (current is KArrayStoreBase<*, *>) {
            depth++
            current = current.array
        }
        return depth
    }

    private fun bvAdderChainSize(root: KBvAddExpr<*>): Int {
        var size = 0
        val stack = ArrayDeque<KBvAddExpr<*>>()
        stack += root
        while (stack.isNotEmpty()) {
            val current = stack.removeLast()
            size++
            current.args.filterIsInstance<KBvAddExpr<*>>().forEach { stack += it }
        }
        return size
    }

    private data class VisitEntry(
        val expression: KExpr<*>,
        val quantifierDepth: Int,
        val parentIsStoreArray: Boolean = false,
        val parentIsBvAdd: Boolean = false,
    )

    private class AstStatistics {
        val operatorCounts = linkedMapOf<String, Long>()
        val selectsPerArray = linkedMapOf<KExpr<*>, Long>()
        val storeChainDepths = mutableListOf<Int>()
        val bvAdderChainSizes = mutableListOf<Int>()
        val quantifierNestingLevels = mutableListOf<Int>()
        val ufAritiesByDecl = linkedMapOf<Any, Int>()
        val ufArities = mutableListOf<Int>()
        val ufApplications = linkedMapOf<Any, Long>()
        var forallBoundVariables: Long = 0
        var existsBoundVariables: Long = 0

        fun incrementOperator(name: String) {
            operatorCounts[name] = operatorCounts.getOrDefault(name, 0L) + 1L
        }
    }

    private companion object {
        const val DEFAULT_MAX_AST_NODE_VISITS = 1_000_000

        val astCountableTokens = setOf(
            "exists", "forall", "true", "false", "not", "=>", "and", "or", "xor", "=", "distinct", "ite",
            "select", "store", "concat", "extract", "bvnot", "bvand", "bvor", "bvneg", "bvadd", "bvmul",
            "bvudiv", "bvurem", "bvshl", "bvlshr", "bvult", "bvnand", "bvnor", "bvxor", "bvxnor", "bvcomp",
            "bvsub", "bvsdiv", "bvsrem", "bvsmod", "bvashr", "repeat", "zero_extend", "sign_extend",
            "rotate_left", "rotate_right", "bvule", "bvugt", "bvuge", "bvslt", "bvsle", "bvsgt", "bvsge",
            "fp", "roundNearestTiesToEven", "roundNearestTiesToAway", "roundTowardPositive", "roundTowardNegative",
            "roundTowardZero", "RNE", "RNA", "RTP", "RTN", "RTZ", "fp.abs", "fp.neg", "fp.add", "fp.sub",
            "fp.mul", "fp.div", "fp.fma", "fp.sqrt", "fp.rem", "fp.roundToIntegral", "fp.min", "fp.max",
            "fp.leq", "fp.lt", "fp.geq", "fp.gt", "fp.eq", "fp.isNormal", "fp.isSubnormal", "fp.isZero",
            "fp.isInfinite", "fp.isNaN", "fp.isNegative", "fp.isPositive", "to_fp", "to_fp_unsigned",
            "fp.to_ubv", "fp.to_sbv", "fp.to_real", "-", "+", "*", "div", "mod", "abs", "<=", "<",
            ">=", ">", "to_real", "to_int", "is_int",
        )
    }
}

private fun Collection<Number>.averageOrZero(): Double =
    if (isEmpty()) 0.0 else sumOf { it.toDouble() } / size

private fun Collection<Number>.medianOrZero(): Double {
    if (isEmpty()) return 0.0
    val sorted = map { it.toDouble() }.sorted()
    val middle = sorted.size / 2
    return if (sorted.size % 2 == 0) {
        (sorted[middle - 1] + sorted[middle]) / 2.0
    } else {
        sorted[middle]
    }
}

private fun Collection<Number>.populationDeviationOrZero(): Double {
    if (isEmpty()) return 0.0
    val mean = averageOrZero()
    return sqrt(sumOf { (it.toDouble() - mean).pow(2) } / size)
}

private fun String.toSmtLibToken(): String = when (this) {
    "implies" -> "=>"
    "arithAdd" -> "+"
    "arithMul" -> "*"
    "arithSub", "arithUnaryMinus" -> "-"
    "arithDiv" -> "div"
    "arithLt" -> "<"
    "arithLe" -> "<="
    "arithGt" -> ">"
    "arithGe" -> ">="
    "intMod", "intRem" -> "mod"
    "intToReal" -> "to_real"
    "realToInt" -> "to_int"
    "realIsInt" -> "is_int"
    "ext_rotate_left" -> "rotate_left"
    "ext_rotate_right" -> "rotate_right"
    "fp.to_fp" -> "to_fp"
    else -> this
}
