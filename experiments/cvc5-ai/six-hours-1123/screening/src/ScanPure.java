import java.util.Arrays;
import java.util.zip.ZipFile;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;
import org.objectweb.asm.tree.ClassNode;

public class ScanPure {
    private static boolean eligibleType(Type t) {
        if (t.getSort() == Type.ARRAY) return t.getDimensions() == 1 && eligibleType(t.getElementType());
        return t.getSort() >= Type.BOOLEAN && t.getSort() <= Type.DOUBLE;
    }

    private static boolean has(Type[] args, int sort) {
        return Arrays.stream(args).anyMatch(type -> type.getSort() == sort);
    }

    public static void main(String[] args) throws Exception {
        String[] prefixes = Arrays.stream(args).skip(1).map(s -> s.replace('.', '/')).toArray(String[]::new);
        try (var zip = new ZipFile(args[0])) {
            var entries = zip.entries();
            while (entries.hasMoreElements()) {
                var entry = entries.nextElement();
                var name = entry.getName();
                if (!name.endsWith(".class") || Arrays.stream(prefixes).noneMatch(name::startsWith)) continue;
                try (var in = zip.getInputStream(entry)) {
                    var type = new ClassNode();
                    new ClassReader(in).accept(type, ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
                    if (type.version > Opcodes.V21 || type.version < 0) continue;
                    for (var method : type.methods) {
                        int excluded = Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE | Opcodes.ACC_BRIDGE
                            | Opcodes.ACC_SYNTHETIC | Opcodes.ACC_SYNCHRONIZED;
                        int required = Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC;
                        if ((method.access & required) != required || (method.access & excluded) != 0
                            || method.name.startsWith("<")) continue;
                        var parameters = Type.getArgumentTypes(method.desc);
                        var returned = Type.getReturnType(method.desc);
                        if (parameters.length == 0 || parameters.length > 6 || !eligibleType(returned)
                            || !Arrays.stream(parameters).allMatch(ScanPure::eligibleType)) continue;
                        int size = 0, branches = 0, mult = 0, loops = 0;
                        for (var insn : method.instructions) {
                            int op = insn.getOpcode();
                            if (op < 0) continue;
                            size++;
                            if ((op >= Opcodes.IFEQ && op <= Opcodes.IF_ACMPNE) || op == Opcodes.IFNULL
                                || op == Opcodes.IFNONNULL || op == Opcodes.TABLESWITCH || op == Opcodes.LOOKUPSWITCH)
                                branches++;
                            if (op == Opcodes.IMUL || op == Opcodes.LMUL || op == Opcodes.IDIV || op == Opcodes.LDIV
                                || op == Opcodes.IREM || op == Opcodes.LREM) mult++;
                            if (insn instanceof org.objectweb.asm.tree.JumpInsnNode j
                                && method.instructions.indexOf(j.label) < method.instructions.indexOf(insn)) loops++;
                        }
                        if (size < 8 || size > 240 || branches == 0) continue;
                        String profile = has(parameters, Type.ARRAY) || returned.getSort() == Type.ARRAY ? "array"
                            : has(parameters, Type.FLOAT) || has(parameters, Type.DOUBLE)
                              || returned.getSort() == Type.FLOAT || returned.getSort() == Type.DOUBLE ? "floating"
                            : "scalar";
                        System.out.printf("%s\t%s\t%s\t%s\t%d\t%d\t%d\t%d%n",
                            type.name.replace('/', '.'), method.name, method.desc, profile, size, branches, mult, loops);
                    }
                }
            }
        }
    }
}
