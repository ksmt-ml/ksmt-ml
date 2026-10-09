package io.ksmt.ml.features

import io.ksmt.KContext
import io.ksmt.KContext.SimplificationMode.NO_SIMPLIFY
import io.ksmt.expr.KExpr
import io.ksmt.sort.KBoolSort
import kotlin.test.Test
import kotlin.test.assertEquals

class MachSmtStructuralFeatureTest {
    @Test
    fun `146 is average select applications per referenced array`() = withContext {
        val arraySort = mkArraySort(intSort, intSort)
        val first = mkConst("first", arraySort)
        val second = mkConst("second", arraySort)
        val i = mkConst("i", intSort)
        val j = mkConst("j", intSort)
        val value = mkConst("value", intSort)

        val assertions = listOf(
            mkArraySelect(first, i) eq value,
            mkArraySelect(first, j) eq value,
            mkArraySelect(second, i) eq value,
        )

        assertEquals(1.5, extract(assertions).feature(146).value)
    }

    @Test
    fun `147 is average maximal store chain depth`() = withContext {
        val arraySort = mkArraySort(intSort, intSort)
        val first = mkConst("first", arraySort)
        val second = mkConst("second", arraySort)
        val index = mkConst("index", intSort)
        val value = mkConst("value", intSort)

        val depthTwo = mkArrayStore(mkArrayStore(first, index, value), index, value)
        val depthFour = mkArrayStore(
            mkArrayStore(
                mkArrayStore(mkArrayStore(second, index, value), index, value),
                index,
                value,
            ),
            index,
            value,
        )

        val assertions = listOf(depthTwo eq first, depthFour eq second)
        assertEquals(3.0, extract(assertions).feature(147).value)
    }

    @Test
    fun `148 is mean of maximal BV adder chain sizes`() {
        assertEquals(3.0, extractBvAdderChainStatistics().feature(148).value)
    }

    @Test
    fun `149 is median of maximal BV adder chain sizes`() {
        assertEquals(3.0, extractBvAdderChainStatistics().feature(149).value)
    }

    @Test
    fun `150 is population deviation of maximal BV adder chain sizes`() {
        assertEquals(1.0, extractBvAdderChainStatistics().feature(150).value)
    }

    private fun extractBvAdderChainStatistics(): MachSmtFeatureResult = withContext {
        val a = mkConst("a", bv32Sort)
        val b = mkConst("b", bv32Sort)
        val c = mkConst("c", bv32Sort)
        val d = mkConst("d", bv32Sort)
        val e = mkConst("e", bv32Sort)
        val result1 = mkConst("result1", bv32Sort)
        val result2 = mkConst("result2", bv32Sort)

        val sizeTwo = mkBvAddExpr(mkBvAddExpr(a, b), c)
        val sizeFour = mkBvAddExpr(
            mkBvAddExpr(mkBvAddExpr(mkBvAddExpr(a, b), c), d),
            e,
        )

        extract(listOf(sizeTwo eq result1, sizeFour eq result2))
    }

    @Test
    fun `151 counts forall bound variables`() = withContext {
        val x = mkConst("x", intSort)
        val y = mkConst("y", intSort)
        val z = mkConst("z", intSort)
        val assertion = mkUniversalQuantifier(trueExpr, listOf(x.decl, y.decl, z.decl))

        assertEquals(3.0, extract(listOf(assertion)).feature(151).value)
    }

    @Test
    fun `152 counts exists bound variables`() = withContext {
        val x = mkConst("x", intSort)
        val y = mkConst("y", intSort)
        val assertion = mkExistentialQuantifier(trueExpr, listOf(x.decl, y.decl))

        assertEquals(2.0, extract(listOf(assertion)).feature(152).value)
    }

    @Test
    fun `153 is exists to forall bound variable ratio`() = withContext {
        val variables = (1..6).map { mkConst("q$it", intSort) }
        val forall = mkUniversalQuantifier(trueExpr, variables.take(4).map { it.decl })
        val exists = mkExistentialQuantifier(trueExpr, variables.takeLast(2).map { it.decl })

        assertEquals(0.5, extract(listOf(forall, exists)).feature(153).value)
    }

    @Test
    fun `153 is zero when no forall variables exist`() = withContext {
        val x = mkConst("x", intSort)
        val exists = mkExistentialQuantifier(trueExpr, listOf(x.decl))

        assertEquals(0.0, extract(listOf(exists)).feature(153).value)
    }

    @Test
    fun `154 is average quantifier nesting level`() = withContext {
        val x = mkConst("x", intSort)
        val y = mkConst("y", intSort)
        val z = mkConst("z", intSort)
        val levelThree = mkUniversalQuantifier(trueExpr, listOf(z.decl))
        val levelTwo = mkExistentialQuantifier(levelThree, listOf(y.decl))
        val levelOne = mkUniversalQuantifier(levelTwo, listOf(x.decl))

        assertEquals(2.0, extract(listOf(levelOne)).feature(154).value)
    }

    @Test
    fun `155 is average arity of applied uninterpreted functions`() = withContext {
        val x = mkConst("x", intSort)
        val y = mkConst("y", intSort)
        val z = mkConst("z", intSort)
        val unary = mkFuncDecl("unary", intSort, listOf(intSort))
        val ternary = mkFuncDecl("ternary", intSort, listOf(intSort, intSort, intSort))

        val assertions = listOf(
            unary.apply(listOf(x)) eq y,
            ternary.apply(listOf(x, y, z)) eq z,
        )

        assertEquals(2.0, extract(assertions).feature(155).value)
    }

    @Test
    fun `156 is average applications per applied uninterpreted function`() = withContext {
        val x = mkConst("x", intSort)
        val y = mkConst("y", intSort)
        val z = mkConst("z", intSort)
        val w = mkConst("w", intSort)
        val first = mkFuncDecl("first", intSort, listOf(intSort))
        val second = mkFuncDecl("second", intSort, listOf(intSort))

        val assertions = listOf(
            first.apply(listOf(x)) eq first.apply(listOf(y)),
            second.apply(listOf(x)) eq second.apply(listOf(y)),
            second.apply(listOf(z)) eq second.apply(listOf(w)),
        )

        assertEquals(3.0, extract(assertions).feature(156).value)
    }

    private fun extract(assertions: List<KExpr<KBoolSort>>): MachSmtFeatureResult =
        MachSmtFeatureExtractor().extract(KsmtAstFeatureInput(assertions = assertions))

    private fun MachSmtFeatureResult.feature(id: Int): MachSmtFeatureValue =
        features.single { it.id == id }

    private fun <T> withContext(block: KContext.() -> T): T =
        KContext(simplificationMode = NO_SIMPLIFY).use { context -> context.block() }
}
