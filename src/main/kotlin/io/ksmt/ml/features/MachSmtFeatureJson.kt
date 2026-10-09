package io.ksmt.ml.features

object MachSmtFeatureJson {
    fun encode(result: MachSmtFeatureResult): String = buildString {
        appendLine("{")
        appendLine("  \"schemaVersion\": ${result.schemaVersion.toJsonString()},")
        appendLine("  \"sourceName\": ${result.sourceName.toJsonStringOrNull()},")
        appendLine("  \"featureCount\": ${result.featureCount},")
        appendLine("  \"vector\": [")
        result.vector.forEachIndexed { index, value ->
            append("    $value")
            if (index != result.vector.lastIndex) append(',')
            appendLine()
        }
        appendLine("  ],")
        appendLine("  \"features\": [")
        result.features.forEachIndexed { index, feature ->
            appendLine("    {")
            appendLine("      \"id\": ${feature.id},")
            appendLine("      \"name\": ${feature.name.toJsonString()},")
            appendLine("      \"cluster\": ${feature.cluster.toJsonString()},")
            appendLine("      \"value\": ${feature.value},")
            appendLine("      \"source\": ${feature.source.toJsonString()}")
            append("    }")
            if (index != result.features.lastIndex) append(',')
            appendLine()
        }
        appendLine("  ],")
        appendLine(
            "  \"unavailableFeatureIds\": " +
                result.unavailableFeatureIds.joinToString(prefix = "[", postfix = "],"),
        )
        appendLine("  \"notes\": [")
        result.notes.forEachIndexed { index, note ->
            append("    ${note.toJsonString()}")
            if (index != result.notes.lastIndex) append(',')
            appendLine()
        }
        appendLine("  ]")
        appendLine("}")
    }

    private fun String?.toJsonStringOrNull(): String = this?.toJsonString() ?: "null"

    private fun String.toJsonString(): String = buildString {
        append('"')
        for (character in this@toJsonString) {
            when (character) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\b' -> append("\\b")
                '\u000C' -> append("\\f")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> if (character.code < 0x20) {
                    append("\\u")
                    append(character.code.toString(16).padStart(4, '0'))
                } else {
                    append(character)
                }
            }
        }
        append('"')
    }
}
