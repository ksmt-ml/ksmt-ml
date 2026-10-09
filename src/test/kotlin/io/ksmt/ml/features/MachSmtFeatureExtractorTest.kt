package io.ksmt.ml.features

import io.ksmt.KContext
import io.ksmt.KContext.SimplificationMode.NO_SIMPLIFY
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class MachSmtFeatureExtractorTest {
    @Test
    fun `extracts a stable 157 element vector from KSMT AST`() {
        KContext(simplificationMode = NO_SIMPLIFY).use { ctx ->
            with(ctx) {
                val arraySort = mkArraySort(bv32Sort, bv32Sort)
                val array = mkConst("a", arraySort)
                val index = mkConst("i", bv32Sort)
                val value = mkConst("v", bv32Sort)
                val storedTwice = mkArrayStore(mkArrayStore(array, index, value), value, index)
                val selected = mkArraySelect(storedTwice, index)

                val x = mkConst("x", bv32Sort)
                val y = mkConst("y", bv32Sort)
                val adderChain = mkBvAddExpr(mkBvAddExpr(x, y), value)

                val bound = mkConst("q", bv32Sort)
                val quantified = mkUniversalQuantifier(
                    mkExistentialQuantifier(bound eq adderChain, listOf(value.decl)),
                    listOf(bound.decl),
                )

                val function = mkFuncDecl("f", bv32Sort, listOf(bv32Sort))
                val functionConstraint = function.apply(listOf(x)) eq function.apply(listOf(y))
                val assertion = (selected eq adderChain) and quantified and functionConstraint
                val result = MachSmtFeatureExtractor().extract(
                    KsmtAstFeatureInput(assertions = listOf(assertion)),
                )

                assertEquals(157, result.featureCount)
                assertEquals(157, result.vector.size)
                assertEquals(1.0, result.feature(146).value)
                assertEquals(2.0, result.feature(147).value)
                assertTrue(result.feature(148).value >= 2.0)
                assertEquals(1.0, result.feature(151).value)
                assertEquals(1.0, result.feature(152).value)
                assertEquals(1.5, result.feature(154).value)
                assertEquals(1.0, result.feature(155).value)
                assertEquals(2.0, result.feature(156).value)
                assertTrue(157 in result.unavailableFeatureIds)
            }
        }
    }

    @Test
    fun `stops pathological AST expansion at the configured visit limit`() {
        KContext(simplificationMode = NO_SIMPLIFY).use { ctx ->
            val expression = with(ctx) { trueExpr and falseExpr }

            val error = assertFailsWith<IllegalStateException> {
                MachSmtFeatureExtractor(maxAstNodeVisits = 1).extract(
                    KsmtAstFeatureInput(assertions = listOf(expression)),
                )
            }

            assertTrue("AST traversal exceeded 1 node visits" in requireNotNull(error.message))
        }
    }

    private fun MachSmtFeatureResult.feature(id: Int): MachSmtFeatureValue =
        features.single { it.id == id }
}
