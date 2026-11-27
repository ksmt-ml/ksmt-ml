"""Apply instrumentation to the isolated checkout copied from the installed campaign."""
from common import USVM

def edit(path, changes):
    file = USVM / path
    text = file.read_text()
    for before, after in changes:
        assert text.count(before) == 1, (file, before)
        text = text.replace(before, after)
    file.write_text(text)

machine = "usvm-jvm/src/main/kotlin/org/usvm/machine"
core = "usvm-core/src/main/kotlin/org/usvm"
edit(f"{machine}/JcSolverFactory.kt", [
    ('            val s = io.ksmt.solver.cvc5.KCvc5Solver(ctx)\n',
     '            val s = Live141Backend.cvc5(ctx)\n'),
    ('                }\n            }\n            org.usvm.solver.ClientStats.createSolverNs',
     '                }\n                Live141Backend.recordZ3(this)\n            }\n            org.usvm.solver.ClientStats.createSolverNs'),
    ])
edit(f"{machine}/JcMachine.kt", [
    ('import org.usvm.machine.state.lastStmt',
     '''import org.usvm.machine.state.lastStmt
import org.jacodb.api.jvm.cfg.JcIfInst
import org.jacodb.api.jvm.cfg.JcSwitchInst
import org.usvm.PathNode'''),
    ('        observers.add(object : UMachineObserver<JcState> {\n            override fun onState(parent: JcState, forks: Sequence<JcState>) {',
     '''        observers.add(object : UMachineObserver<JcState> {
            private var entryBranch: PathNode<JcInst>? = null

            override fun onStatePeeked(state: JcState) {
                val stmt = state.lastStmt
                entryBranch = if (stmt.location.method == state.entrypoint &&
                    (stmt is JcIfInst || stmt is JcSwitchInst)
                ) state.pathNode else null
            }

            private fun observeBranch(source: PathNode<JcInst>, state: JcState) {
                val destination = state.pathNode
                if (destination.parent !== source) return
                val stmt = source.statement
                val next = destination.statement
                if (next.location.method != stmt.location.method) return
                val to = next.location.index
                val isTarget = when (stmt) {
                    is JcIfInst -> to == stmt.trueBranch.index || to == stmt.falseBranch.index
                    is JcSwitchInst -> to == stmt.default.index || stmt.branches.values.any { it.index == to }
                    else -> false
                }
                if (isTarget) org.usvm.solver.ClientStats.branchEdges.add("${stmt.location.index}:$to")
            }

            override fun onState(parent: JcState, forks: Sequence<JcState>) {
                entryBranch?.let { source ->
                    observeBranch(source, parent)
                    forks.forEach { observeBranch(source, it) }
                }'''),
    ('                org.usvm.solver.ClientStats.terminated++',
     '''                org.usvm.solver.ClientStats.terminated++
                if (state.callStack.isEmpty()) {
                    org.usvm.solver.ClientStats.completedPaths++
                    when (val result = state.methodResult) {
                        is JcMethodResult.Success -> org.usvm.solver.ClientStats.returnedPaths++
                        is JcMethodResult.JcException ->
                            org.usvm.solver.ClientStats.exceptionTypes.merge(result.type.typeName, 1, Int::plus)
                        else -> Unit
                    }
                }'''),
    ])
edit(f"{core}/solver/ClientStats.kt", [
    ('    @JvmField var coveragePct = 0.0', '''    @JvmField var coveragePct = 0.0
    var completedPaths = 0L
    var returnedPaths = 0L
    var stopReason = "queue_exhausted"
    val branchEdges = sortedSetOf<String>()
    val exceptionTypes = sortedMapOf<String, Int>()
    val unknownReasons = sortedMapOf<String, Int>()
    var afterFirstCheck: (() -> Unit)? = null'''),
    ('        reset()\n        return s',
     '''        val json = kotlinx.serialization.json.Json
        val extra = ",\\"stop_reason\\":\\"${stopReason}\\",\\"branch_edges\\":${json.encodeToString(branchEdges.toList())}" +
            ",\\"completed_paths\\":$completedPaths,\\"returned_paths\\":$returnedPaths" +
            ",\\"exception_types\\":${json.encodeToString<Map<String, Int>>(exceptionTypes)}" +
            ",\\"unknown_reasons\\":${json.encodeToString<Map<String, Int>>(unknownReasons)}"
        reset()
        return s.dropLast(1) + extra + "}"'''),
    ('        checkMsList = ArrayList(); assertMsPerCheckList = ArrayList()',
     '''        checkMsList = ArrayList(); assertMsPerCheckList = ArrayList()
        completedPaths = 0L; returnedPaths = 0L; stopReason = "queue_exhausted"
        branchEdges.clear(); exceptionTypes.clear(); unknownReasons.clear()'''),
    ('        val status = run()\n        val d = System.nanoTime() - t0',
     '''        val status = run()
        val d = System.nanoTime() - t0
        ClientStats.afterFirstCheck?.let {
            ClientStats.afterFirstCheck = null
            it()
        }'''),
    ('            KSolverStatus.UNKNOWN -> { ClientStats.unknown++; ClientStats.unknownNs += d }',
     '''            KSolverStatus.UNKNOWN -> {
                ClientStats.unknown++
                ClientStats.unknownNs += d
                ClientStats.unknownReasons.merge(delegate.reasonOfUnknown(), 1, Int::plus)
            }'''),
    ])
stop = f"{core}/stopstrategies/StopStrategyFactory.kt"
edit(stop, [
    ('    val stopStrategies = mutableListOf<StopStrategy>()',
     '''    val stopStrategies = mutableListOf<Pair<String, StopStrategy>>()'''),
    ('stopStrategies.add(StepLimitStopStrategy(stepLimit, stepsStatistics.value))',
     'stopStrategies.add("step_limit" to StepLimitStopStrategy(stepLimit, stepsStatistics.value))'),
    ('stopStrategies.add(coverageStopStrategy)', 'stopStrategies.add("coverage_goal" to coverageStopStrategy)'),
    ('stopStrategies.add(collectedStatesCountStopStrategy)', 'stopStrategies.add("states_limit" to collectedStatesCountStopStrategy)'),
    ('stopStrategies.add(TimeoutStopStrategy(options.timeout, timeStatistics))',
     'stopStrategies.add("method_timeout" to TimeoutStopStrategy(options.timeout, timeStatistics))'),
    ('stopStrategies.add(stepsFromLastCoveredStopStrategy)', 'stopStrategies.add("stagnation" to stepsFromLastCoveredStopStrategy)'),
    ('stopStrategies.add(TargetsReachedStopStrategy(targets))', 'stopStrategies.add("targets" to TargetsReachedStopStrategy(targets))'),
    ('''    if (stopStrategies.size == 1) {
        return stopStrategies.first()
    }
    return GroupedStopStrategy(stopStrategies)''',
     '''    if (!org.usvm.solver.ClientStats.enabled) {
        val delegates = stopStrategies.map { it.second }
        return delegates.singleOrNull() ?: GroupedStopStrategy(delegates)
    }
    return object : StopStrategy {
        override fun shouldStop(): Boolean {
            val match = stopStrategies.firstOrNull { it.second.shouldStop() } ?: return false
            org.usvm.solver.ClientStats.stopReason = match.first
            return true
        }

        override fun stopReason(): String = org.usvm.solver.ClientStats.stopReason
    }'''),
    ])
