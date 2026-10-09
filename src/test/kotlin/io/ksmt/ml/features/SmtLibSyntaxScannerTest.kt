package io.ksmt.ml.features

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class SmtLibSyntaxScannerTest {
    @Test
    fun `counts grammar tokens and ignores comments strings and quoted symbols`() {
        val input = """
            ; (assert false)
            (set-logic QF_BV)
            (declare-const |assert| (_ BitVec 8))
            (assert (= (bvadd x x) x))
            (echo "assert bvadd")
            (check-sat)
        """.trimIndent()

        val counts = SmtLibSyntaxScanner.countTokens(input)

        assertEquals(1L, counts["set-logic"])
        assertEquals(1L, counts["BitVec"])
        assertEquals(1L, counts["assert"])
        assertEquals(1L, counts["bvadd"])
        assertEquals(1L, counts["check-sat"])
        assertNull(counts["false"])
    }

    @Test
    fun `extracts status without accepting comments strings or quoted symbols`() {
        val input = """
            ; (set-info :status unsat)
            (echo "(set-info :status unknown)")
            (declare-const |set-info :status unsat| Bool)
            (set-info :status sat)
        """.trimIndent()

        assertEquals("sat", SmtLibSyntaxScanner.findExpectedStatus(input))
    }

    @Test
    fun `rejects conflicting statuses`() {
        val input = """
            (set-info :status sat)
            (set-info :status unsat)
        """.trimIndent()

        assertNull(SmtLibSyntaxScanner.findExpectedStatus(input))
    }

    @Test
    fun `rejects nested status shaped expression`() {
        val input = "(assert (= (set-info :status sat) true))"

        assertNull(SmtLibSyntaxScanner.findExpectedStatus(input))
    }
}
