package io.ksmt.ml.features

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class MachSmtFeatureProfileTest {
    @Test
    fun `every feature belongs to exactly one cluster`() {
        val definitions = MachSmtFeatureSchema.definitions

        assertEquals((1..157).toList(), definitions.map { it.id })
        assertEquals(157, definitions.map { it.name }.distinct().size)
    }

    @Test
    fun `QF ALIA baseline contains only relevant 36 features`() {
        val ids = MachSmtFeatureProfiles.featureIds(MachSmtFeatureProfile.QF_ALIA_BASELINE)

        assertEquals(36, ids.size)
        assertTrue(2 in ids)
        assertTrue(5 in ids)
        assertTrue(8 in ids)
        assertTrue((35..48).all { it in ids })
        assertTrue((131..147).all { it in ids })
        assertTrue(157 in ids)
        assertFalse((49..130).any { it in ids })
        assertFalse((151..156).any { it in ids })
    }

    @Test
    fun `QF AUFLIA baseline extends ALIA with UF features`() {
        val alia = MachSmtFeatureProfiles.featureIds(MachSmtFeatureProfile.QF_ALIA_BASELINE).toSet()
        val auflia = MachSmtFeatureProfiles.featureIds(MachSmtFeatureProfile.QF_AUFLIA_BASELINE).toSet()

        assertEquals(41, auflia.size)
        assertTrue(auflia.containsAll(alia))
        assertTrue(setOf(10, 11, 12, 155, 156).all { it in auflia })
    }
}
