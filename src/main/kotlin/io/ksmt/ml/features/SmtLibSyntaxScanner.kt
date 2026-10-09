package io.ksmt.ml.features

/**
 * Lightweight SMT-LIB lexer used only for token frequencies. It deliberately
 * does not build a second AST. Comments, string literals, and quoted symbols
 * are skipped so their contents cannot be mistaken for grammar constructs.
 */
object SmtLibSyntaxScanner {
    fun countTokens(input: CharSequence): Map<String, Long> {
        val counts = linkedMapOf<String, Long>()

        scanTokens(input) { token ->
            if (token != "(" && token != ")") {
                counts[token] = counts.getOrDefault(token, 0L) + 1L
            }
        }

        return counts
    }

    /**
     * Returns the unique top-level metadata status, or null when it is absent
     * or conflicting. Comments, strings, and quoted symbols are ignored by the
     * same lexer used for feature counts.
     */
    fun findExpectedStatus(input: CharSequence): String? {
        val statuses = linkedSetOf<String>()
        var depth = 0
        var command: MutableList<String>? = null

        scanTokens(input) { token ->
            when (token) {
                "(" -> {
                    if (depth == 0) command = mutableListOf("(")
                    else command?.add("(")
                    depth++
                }
                ")" -> {
                    if (depth > 0) depth--
                    command?.add(")")
                    if (depth == 0) {
                        command
                            ?.takeIf {
                                it.size == 5 &&
                                    it[0] == "(" &&
                                    it[1] == "set-info" &&
                                    it[2] == ":status" &&
                                    it[4] == ")"
                            }
                            ?.get(3)
                            ?.lowercase()
                            ?.takeIf { it in EXPECTED_STATUSES }
                            ?.let(statuses::add)
                        command = null
                    }
                }
                else -> command?.add(token)
            }
        }

        return statuses.singleOrNull()
    }

    private fun scanTokens(input: CharSequence, consume: (String) -> Unit) {
        var index = 0

        while (index < input.length) {
            when (val current = input[index]) {
                ';' -> index = skipComment(input, index + 1)
                '"' -> index = skipString(input, index + 1)
                '|' -> index = skipQuotedSymbol(input, index + 1)
                '(', ')' -> {
                    consume(current.toString())
                    index++
                }
                else -> {
                    if (current.isWhitespace()) {
                        index++
                    } else {
                        val start = index
                        while (index < input.length && !input[index].isTokenDelimiter()) {
                            index++
                        }
                        val token = input.subSequence(start, index).toString()
                        consume(token)
                    }
                }
            }
        }
    }

    private fun skipComment(input: CharSequence, start: Int): Int {
        var index = start
        while (index < input.length && input[index] != '\n' && input[index] != '\r') {
            index++
        }
        return index
    }

    private fun skipString(input: CharSequence, start: Int): Int {
        var index = start
        while (index < input.length) {
            if (input[index] == '"') {
                if (index + 1 < input.length && input[index + 1] == '"') {
                    index += 2
                } else {
                    return index + 1
                }
            } else {
                index++
            }
        }
        return index
    }

    private fun skipQuotedSymbol(input: CharSequence, start: Int): Int {
        var index = start
        while (index < input.length && input[index] != '|') {
            index++
        }
        return if (index < input.length) index + 1 else index
    }

    private fun Char.isTokenDelimiter(): Boolean =
        isWhitespace() || this == '(' || this == ')' || this == ';' || this == '"' || this == '|'

    private val EXPECTED_STATUSES = setOf("sat", "unsat", "unknown")
}
