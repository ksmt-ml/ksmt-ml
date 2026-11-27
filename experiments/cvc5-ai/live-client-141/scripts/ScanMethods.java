import java.util.zip.ZipFile;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.MethodNode;

class ScanMethods {
    public static void main(String[] args) throws Exception {
        try (var jar = new ZipFile(args[0])) {
            var entries = jar.entries();
            String prefix = args[1].replace('.', '/');
            while (entries.hasMoreElements()) {
                var entry = entries.nextElement();
                String name = entry.getName();
                if (!name.startsWith(prefix) || !name.endsWith(".class")) continue;
                try (var bytes = jar.getInputStream(entry)) {
                    var type = new ClassNode();
                    new ClassReader(bytes).accept(type, ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
                    if (type.version > Opcodes.V21 || type.version < 0) continue;
                    for (MethodNode method : type.methods) {
                        int excluded = Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE | Opcodes.ACC_BRIDGE | Opcodes.ACC_SYNTHETIC;
                        if ((method.access & Opcodes.ACC_PUBLIC) == 0 || (method.access & excluded) != 0 ||
                            method.name.startsWith("<") || Type.getArgumentTypes(method.desc).length == 0) continue;
                        int size = 0, branches = 0;
                        for (AbstractInsnNode insn : method.instructions) {
                            int op = insn.getOpcode();
                            if (op < 0) continue;
                            size++;
                            if ((op >= Opcodes.IFEQ && op <= Opcodes.IF_ACMPNE) ||
                                op == Opcodes.IFNULL || op == Opcodes.IFNONNULL ||
                                op == Opcodes.TABLESWITCH || op == Opcodes.LOOKUPSWITCH) branches++;
                        }
                        if (size >= 8 && size <= 260 && branches > 0)
                            System.out.printf("%s\t%s\t%s\t%d\t%d%n", type.name.replace('/', '.'),
                                method.name, method.desc, size, branches);
                    }
                }
            }
        }
    }
}
