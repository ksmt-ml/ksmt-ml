import io.github.cvc5.Solver;
import io.ksmt.KContext;
import io.ksmt.solver.cvc5.KCvc5Solver;

class NativeOptionsProbe {
    public static void main(String[] args) throws Exception {
        String mode = args[0];
        try (KContext context = new KContext(); KCvc5Solver client = new KCvc5Solver(context)) {
            Solver solver = client.nativeSolver();
            System.out.println("version\t" + solver.getVersion());
            for (int i = 1; i < args.length; i++) {
                String[] pair = args[i].split("=", 2);
                String name = pair[0];
                try {
                    String before = solver.getOption(name);
                    solver.setOption(name, pair[1]);
                    System.out.println("option\t" + name + "\t" + before + "\t" + solver.getOption(name));
                } catch (Exception ex) {
                    System.out.println("unsupported\t" + name + "\t" + ex.getMessage().replace('\n', ' '));
                    if (!mode.equals("probe")) {
                        System.exit(3);
                    }
                }
            }
            solver.setOption("incremental", "true");
            solver.setOption("produce-models", "true");
            solver.setOption("produce-unsat-cores", "true");
            var proposition = solver.mkConst(solver.getBooleanSort(), "night_control");
            solver.assertFormula(proposition);
            if (!solver.checkSat().isSat() || !solver.getValue(proposition).equals(solver.mkBoolean(true))) {
                throw new IllegalStateException("sat or model mismatch");
            }
            solver.push();
            solver.assertFormula(solver.mkBoolean(false));
            if (!solver.checkSat().isUnsat() || solver.getUnsatCore().length == 0) {
                throw new IllegalStateException("unsat or core mismatch");
            }
            solver.pop();
            if (!solver.checkSat().isSat()) {
                throw new IllegalStateException("incremental state mismatch");
            }
            System.out.println("smoke\tsat\tunsat\tsat\tmodel\tcore");
        }
    }
}
