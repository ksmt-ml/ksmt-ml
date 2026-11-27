"""Make the fresh witness driver, leaving the measured USVM client unchanged."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def edit(body,old,new):
    assert body.count(old)==1,old
    return body.replace(old,new)

def main():
    source=ROOT.parent/"followup/src/PowerDriver.kt"
    text=source.read_text()
    text=edit(text,"        throwExceptionOnStepFailure = false,",
              "        throwExceptionOnStepFailure = false,\n        stopOnCoverage = 0,\n        stateCollectionStrategy = StateCollectionStrategy.ALL,")
    text=edit(text,"    var message = \"\"",
              "    var message = \"\"\n    val dataWitness = mutableListOf<kotlinx.serialization.json.JsonObject>()")
    text=edit(text,'''            count = machine.analyze(m).size
            analysis = System.nanoTime() - t0''', '''            val states = machine.analyze(m)
            count = states.size
            analysis = System.nanoTime() - t0
            if (tag.startsWith("ieee/")) {
                val inputClass = m.enclosingClass.name
                val type = cp.findTypeOrNull(inputClass) as JcClassType
                val typed = type.declaredMethods.single { it.method == m }
                val library = File(requireNotNull(System.getenv("SMT_ORACLE_FIXTURE")))
                URLClassLoader(arrayOf(library.toURI().toURL()), ClassLoader.getPlatformClassLoader()).use { loader ->
                    val resolver = JcTestInterpreter(loader)
                    val signature = java.lang.invoke.MethodType.fromMethodDescriptorString(m.description, loader)
                    val target = loader.loadClass(inputClass).getMethod(m.name, *signature.parameterArray())
                    for (state in states.take(64)) {
                        val proof = buildJsonObject {
                            try {
                                val test = resolver.resolve(typed, state)
                                val symbolic = test.result.getOrThrow() as Int
                                val concrete = target.invoke(null, test.before.parameters[0]) as Int
                                put("expected", symbolic)
                                put("actual", concrete)
                                put("valid", concrete == symbolic)
                            } catch (e: Exception) {
                                put("error", e.javaClass.name)
                                put("message", e.message.orEmpty().take(160))
                            }
                        }
                        dataWitness.add(proof)
                    }
                }
            }''')
    text=edit(text,'        put("stats", stats)', '''        put("stats", stats)
        put("witnesses", kotlinx.serialization.json.JsonArray(dataWitness))''')
    (ROOT/"src/LiveOracle.kt").write_text(text)

if __name__=="__main__":
    main()
