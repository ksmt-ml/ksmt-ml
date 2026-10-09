package io.ksmt.ml.features

enum class MachSmtFeatureProfile {
    ALL,
    THEORY_RELEVANT,
    QF_ALIA_BASELINE,
    QF_AUFLIA_BASELINE,
}

object MachSmtFeatureProfiles {
    fun featureIds(profile: MachSmtFeatureProfile): List<Int> = when (profile) {
        MachSmtFeatureProfile.ALL ->
            MachSmtFeatureSchema.definitions.map { it.id }

        MachSmtFeatureProfile.THEORY_RELEVANT ->
            MachSmtFeatureSchema.definitions
                .filterNot { definition ->
                    definition.cluster in setOf(
                        FeatureCluster.PROBLEM_COMMANDS,
                        FeatureCluster.CONTROL_COMMANDS,
                    )
                }
                .map { it.id }

        MachSmtFeatureProfile.QF_ALIA_BASELINE -> qfAliaFeatureIds
        MachSmtFeatureProfile.QF_AUFLIA_BASELINE -> qfAufliaFeatureIds
    }

    fun select(
        result: MachSmtFeatureResult,
        profile: MachSmtFeatureProfile,
    ): List<MachSmtFeatureValue> {
        val selectedIds = featureIds(profile).toSet()
        return result.features.filter { it.id in selectedIds }
    }

    private val sharedQfArrayArithmeticIds: List<Int> = buildList {
        add(2) // assert
        add(5) // declare-const
        add(8) // declare-fun also declares constants when its argument list is empty
        add(34) // let
        addAll(35..48) // Boolean core and arrays
        addAll(131..147) // arithmetic and array structural features
        add(157) // source size
    }

    private val qfAliaFeatureIds: List<Int> = sharedQfArrayArithmeticIds

    private val qfAufliaFeatureIds: List<Int> = buildList {
        addAll(sharedQfArrayArithmeticIds)
        addAll(listOf(10, 11, 12)) // function definitions
        addAll(155..156) // UF arity and applications
    }.sorted()
}
