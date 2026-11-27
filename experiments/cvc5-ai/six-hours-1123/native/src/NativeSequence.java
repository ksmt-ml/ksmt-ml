import io.github.cvc5.Command;
import io.github.cvc5.InputParser;
import io.github.cvc5.Solver;
import io.github.cvc5.TermManager;
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

public final class NativeSequence {
    private static final List<String> REPORTED = List.of(
        "arrays-eager-index", "arrays-exp", "bv-eager-eval", "bv-to-bool", "simplification",
        "static-learning", "incremental", "produce-models", "produce-unsat-cores", "seed", "tlimit-per"
    );
    private static final String CHECK = "check-sat";

    private static void effective(Solver solver, String phase, Map<String,String> expected) {
        for (String name : REPORTED) {
            String value = solver.getOption(name);
            System.out.printf("OPTION\t%s\t%s\t%s%n", phase, name, value);
            if (expected.containsKey(name) && !expected.get(name).equals(value)) {
                throw new IllegalStateException("Native option differs " + name + " " + value);
            }
        }
    }

    public static void main(String[] args) throws Exception {
        System.load(System.getProperty("cvc5jni"));
        Path input = Path.of(args[0]);
        Map<String,String> requested = new TreeMap<>(Map.of(
            "incremental", "true", "produce-models", "true", "produce-unsat-cores", "true", "seed", "0", "tlimit-per", "1600"
        ));
        for (int i = 1; i < args.length; i++) {
            String[] pair = args[i].split("=", 2);
            if (pair.length != 2 || !REPORTED.contains(pair[0])) {
                throw new IllegalArgumentException(args[i]);
            }
            requested.put(pair[0], pair[1]);
        }
        TermManager terms = new TermManager();
        Solver solver = new Solver(terms);
        for (var entry : requested.entrySet()) solver.setOption(entry.getKey(), entry.getValue());
        System.out.printf("RUNTIME\t%s\t%s\t%d%n", solver.getVersion(), System.getProperty("java.runtime.version"),
                          Runtime.getRuntime().availableProcessors());
        try (var lines = Files.lines(Path.of("/proc/self/status"))) {
            System.out.println(lines.filter(l -> l.startsWith("Cpus_allowed_list:")).findFirst().orElseThrow());
        }
        try (var lines = Files.lines(Path.of("/proc/self/maps"))) {
            lines.filter(l -> l.contains("/libcvc5jni.so")).forEach(l -> System.out.println("MAP\t" + l));
        }
        effective(solver, "before", requested);
        InputParser parser = new InputParser(solver);
        parser.setFileInput(io.github.cvc5.modes.InputLanguage.SMT_LIB_2_6, input.toString());
        var symbols = parser.getSymbolManager();
        var bean = ManagementFactory.getThreadMXBean();
        if (!bean.isCurrentThreadCpuTimeSupported()) throw new IllegalStateException("thread cpu unavailable");
        int checks = 0;
        for (Command command = parser.nextCommand(); !command.isNull(); command = parser.nextCommand()) {
            boolean check = command.getCommandName().equals(CHECK);
            long beforeCpu = bean.getCurrentThreadCpuTime();
            long beforeWall = System.nanoTime();
            String result = command.invoke(solver, symbols).strip();
            long wall = System.nanoTime() - beforeWall;
            long cpu = bean.getCurrentThreadCpuTime() - beforeCpu;
            if (check) {
                checks++;
                String reason = "";
                if (result.equals("unknown")) {
                    try {
                        reason = solver.getInfo("reason-unknown").replace('\n', ' ').replace('\t', ' ');
                    } catch (RuntimeException unavailable) {
                        reason = "reason unavailable";
                    }
                }
                System.out.printf("CHECK\t%d\t%s\t%d\t%d\t%s%n", checks, result, wall, cpu, reason);
                if (checks == 1) effective(solver, "after", requested);
            } else if (!result.isEmpty()) {
                throw new IllegalStateException(command.getCommandName() + " " + result);
            }
        }
        System.out.printf("END\t%d%n", checks);
    }
}
