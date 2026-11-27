"""Use the JVM current thread's CPU clock alongside the original wall clock."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def one(text,old,new):
    assert text.count(old)==1,old
    return text.replace(old,new)

def main():
    old=ROOT.parent/"followup/src"
    w=(old/"WorkTrace.kt").read_text()
    w=one(w,"import java.security.MessageDigest","import java.security.MessageDigest\nimport java.lang.management.ManagementFactory")
    w=one(w,"    private val digest = MessageDigest.getInstance",'''    private val clock = ManagementFactory.getThreadMXBean()
    private var cpuInjection = 0L
    private var cpuChecks = 0L

    fun cpu(): Long {
        check(clock.isCurrentThreadCpuTimeSupported)
        return clock.currentThreadCpuTime.also { check(it >= 0L) }
    }

    fun countCpu(from: Long) {
        cpuChecks += cpu() - from
    }

    private val digest = MessageDigest.getInstance''')
    w=one(w,"        injectedNs = 0","        injectedNs = 0\n        cpuInjection = 0\n        cpuChecks = 0")
    old_inject='''        val start = System.nanoTime()
        var duration: Long
        do {
            Thread.onSpinWait()
            duration = System.nanoTime() - start
        } while (duration < delayNs)
        injectedNs += duration'''
    new_inject='''        val start = System.nanoTime()
        val before = cpu()
        var duration: Long
        do {
            repeat(16) { Thread.onSpinWait() }
            duration = cpu() - before
        } while (duration < delayNs)
        cpuInjection += duration
        injectedNs += System.nanoTime() - start'''
    w=one(w,old_inject,new_inject)
    w=one(w,'        ",\\"injected_ms\\":${injectedNs / 1e6},\\"trace_enabled\\":$enabled" +',
      '''        ",\\"injected_ms\\":${injectedNs / 1e6},\\"trace_enabled\\":$enabled,\\"injected_cpu_ms\\":${cpuInjection / 1e6}," +
            "\\"check_thread_cpu_ms\\":${cpuChecks / 1e6}" +''')
    c=(old/"ClientStats.kt").read_text()
    c=one(c,'''        WorkTrace.inject()
        val status = run()
        val d = System.nanoTime() - t0''',
        '''        val beforeCpu = WorkTrace.cpu()
        WorkTrace.inject()
        val status = run()
        WorkTrace.countCpu(beforeCpu)
        val d = System.nanoTime() - t0''')
    (ROOT/"src/WorkTrace.kt").write_text(w)
    (ROOT/"src/ClientStats.kt").write_text(c)

if __name__=="__main__":
    main()
