package io.ksmt.ml.features

internal object MachSmtFeatureSchema {
    /**
     * The token set follows the corrected STTT 2023 non-string groups and the
     * historical pre-string MachSMT list. Accidental duplicate get-* entries
     * in that implementation are removed, leaving 145 unique grammar slots.
     */
    private val grammarTokens: List<String> = listOf(
        "as",
        "assert",
        "check-sat",
        "check-sat-assuming",
        "declare-const",
        "declare-datatype",
        "declare-datatypes",
        "declare-fun",
        "declare-sort",
        "define-fun",
        "define-fun-rec",
        "define-funs-rec",
        "define-sort",
        "echo",
        "exit",
        "get-assertions",
        "get-assignment",
        "get-info",
        "get-model",
        "get-option",
        "get-proof",
        "get-unsat-assumptions",
        "get-unsat-core",
        "get-value",
        "pop",
        "push",
        "reset",
        "reset-assertions",
        "set-info",
        "set-logic",
        "set-option",
        "exists",
        "forall",
        "let",
        "true",
        "false",
        "not",
        "=>",
        "and",
        "or",
        "xor",
        "=",
        "distinct",
        "ite",
        "Bool",
        "Array",
        "select",
        "store",
        "BitVec",
        "concat",
        "extract",
        "bvnot",
        "bvand",
        "bvor",
        "bvneg",
        "bvadd",
        "bvmul",
        "bvudiv",
        "bvurem",
        "bvshl",
        "bvlshr",
        "bvult",
        "bvnand",
        "bvnor",
        "bvxor",
        "bvxnor",
        "bvcomp",
        "bvsub",
        "bvsdiv",
        "bvsrem",
        "bvsmod",
        "bvashr",
        "repeat",
        "zero_extend",
        "sign_extend",
        "rotate_left",
        "rotate_right",
        "bvule",
        "bvugt",
        "bvuge",
        "bvslt",
        "bvsle",
        "bvsgt",
        "bvsge",
        "RoundingMode",
        "FloatingPoint",
        "Float16",
        "Float32",
        "Float64",
        "Float128",
        "fp",
        "roundNearestTiesToEven",
        "roundNearestTiesToAway",
        "roundTowardPositive",
        "roundTowardNegative",
        "roundTowardZero",
        "RNE",
        "RNA",
        "RTP",
        "RTN",
        "RTZ",
        "fp.abs",
        "fp.neg",
        "fp.add",
        "fp.sub",
        "fp.mul",
        "fp.div",
        "fp.fma",
        "fp.sqrt",
        "fp.rem",
        "fp.roundToIntegral",
        "fp.min",
        "fp.max",
        "fp.leq",
        "fp.lt",
        "fp.geq",
        "fp.gt",
        "fp.eq",
        "fp.isNormal",
        "fp.isSubnormal",
        "fp.isZero",
        "fp.isInfinite",
        "fp.isNaN",
        "fp.isNegative",
        "fp.isPositive",
        "to_fp",
        "to_fp_unsigned",
        "fp.to_ubv",
        "fp.to_sbv",
        "fp.to_real",
        "Int",
        "-",
        "+",
        "*",
        "div",
        "mod",
        "abs",
        "<=",
        "<",
        ">=",
        ">",
        "to_real",
        "to_int",
        "is_int",
        "Real",
    )

    val definitions: List<FeatureDefinition> = buildList {
        require(grammarTokens.size == 145)

        grammarTokens.forEachIndexed { index, token ->
            val id = index + 1
            add(
                FeatureDefinition(
                    id = id,
                    name = featureName(id, token),
                    cluster = grammarCluster(id),
                    token = token,
                )
            )
        }

        add(FeatureDefinition(146, featureName(146, "average-selects-per-array"), FeatureCluster.ARRAYS))
        add(FeatureDefinition(147, featureName(147, "average-store-chain-depth"), FeatureCluster.ARRAYS))
        add(FeatureDefinition(148, featureName(148, "bv-adder-chain-mean"), FeatureCluster.BIT_VECTORS))
        add(FeatureDefinition(149, featureName(149, "bv-adder-chain-median"), FeatureCluster.BIT_VECTORS))
        add(FeatureDefinition(150, featureName(150, "bv-adder-chain-population-deviation"), FeatureCluster.BIT_VECTORS))
        add(
            FeatureDefinition(
                151,
                featureName(151, "forall-bound-variables"),
                FeatureCluster.QUANTIFIERS_AND_BINDINGS,
            )
        )
        add(
            FeatureDefinition(
                152,
                featureName(152, "exists-bound-variables"),
                FeatureCluster.QUANTIFIERS_AND_BINDINGS,
            )
        )
        add(
            FeatureDefinition(
                153,
                featureName(153, "exists-to-forall-variable-ratio"),
                FeatureCluster.QUANTIFIERS_AND_BINDINGS,
            )
        )
        add(
            FeatureDefinition(
                154,
                featureName(154, "average-quantifier-nesting"),
                FeatureCluster.QUANTIFIERS_AND_BINDINGS,
            )
        )
        add(
            FeatureDefinition(
                155,
                featureName(155, "average-applied-uf-arity"),
                FeatureCluster.UNINTERPRETED_FUNCTIONS,
            )
        )
        add(
            FeatureDefinition(
                156,
                featureName(156, "average-applications-per-applied-uf"),
                FeatureCluster.UNINTERPRETED_FUNCTIONS,
            )
        )
        add(FeatureDefinition(157, featureName(157, "source-size-bytes"), FeatureCluster.SOURCE_METADATA))

        require(size == KSMT_MACHSMT_INSPIRED_FEATURE_COUNT)
    }

    private fun featureName(id: Int, rawName: String): String =
        "f${id.toString().padStart(3, '0')}_${rawName.toFeatureSlug()}"

    private fun grammarCluster(id: Int): FeatureCluster = when (id) {
        in 1..4 -> FeatureCluster.PROBLEM_COMMANDS
        in 5..13 -> FeatureCluster.DECLARATIONS
        in 14..31 -> FeatureCluster.CONTROL_COMMANDS
        in 32..34 -> FeatureCluster.QUANTIFIERS_AND_BINDINGS
        in 35..45 -> FeatureCluster.BOOLEAN_CORE
        in 46..48 -> FeatureCluster.ARRAYS
        in 49..84 -> FeatureCluster.BIT_VECTORS
        in 85..130 -> FeatureCluster.FLOATING_POINT
        in 131..145 -> FeatureCluster.ARITHMETIC
        else -> error("Unexpected grammar feature id: $id")
    }

    private fun String.toFeatureSlug(): String = buildString {
        for (character in this@toFeatureSlug) {
            when {
                character.isLetterOrDigit() -> append(character.lowercaseChar())
                character == '+' -> append("plus")
                character == '*' -> append("mul")
                character == '=' -> append("eq")
                character == '<' -> append("lt")
                character == '>' -> append("gt")
                character == '-' || character == '.' -> append('-')
                else -> append('-')
            }
        }
    }.replace(Regex("-+"), "-").trim('-')
}
