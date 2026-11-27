package examples.kernels;
import java.util.TreeSet;
import java.util.stream.Collectors;

public final class Oracle {
    private static void verify(String group, String name, int w, int k) throws Exception {
        Class<?> cls = Class.forName("examples.kernels." + name);
        var method = cls.getMethod("run", int.class, int.class);
        var values = new TreeSet<Integer>();
        int stop = 1 << w;
        for (int x = 0; x < stop; x++) {
            for (int y = 0; y < stop; y++) {
                int actual = (Integer) method.invoke(null,x,y);
                if (actual == -1) throw new IllegalStateException(name + " violated invariant");
                values.add(actual);
            }
        }
        System.out.printf("%s	%s	%d	%d	%s%n", group,name,w,k,
                values.stream().map(String::valueOf).collect(Collectors.joining(",")));
    }
    public static void main(String[] args) throws Exception {
        verify("partial", "Partial1", 8, 58);
        verify("partial", "Partial2", 6, 33);
        verify("partial", "Partial3", 7, 64);
        verify("partial", "Partial4", 8, 244);
        verify("division", "Division1", 8, 33);
        verify("division", "Division2", 6, 36);
        verify("division", "Division3", 7, 92);
        verify("division", "Division4", 8, 132);
        verify("carry", "Carry1", 8, 193);
        verify("carry", "Carry2", 6, 44);
        verify("carry", "Carry3", 7, 91);
        verify("carry", "Carry4", 8, 96);
        verify("overwrite", "Overwrite1", 8, 17);
        verify("overwrite", "Overwrite2", 6, 54);
        verify("overwrite", "Overwrite3", 7, 35);
        verify("overwrite", "Overwrite4", 8, 219);
        verify("signed", "Signed1", 8, 172);
        verify("signed", "Signed2", 6, 52);
        verify("signed", "Signed3", 7, 88);
        verify("signed", "Signed4", 8, 110);
        verify("packing", "Packing1", 8, 246);
        verify("packing", "Packing2", 6, 3);
        verify("packing", "Packing3", 7, 32);
        verify("packing", "Packing4", 8, 12);
    }
}
