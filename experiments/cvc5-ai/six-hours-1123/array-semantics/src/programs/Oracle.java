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
        verify("sparse", "Sparse1", 5, 2);
        verify("sparse", "Sparse2", 6, 20);
        verify("sparse", "Sparse3", 7, 36);
        verify("sparse", "Sparse4", 6, 49);
        verify("copy", "Copy1", 5, 15);
        verify("copy", "Copy2", 6, 62);
        verify("copy", "Copy3", 7, 53);
        verify("copy", "Copy4", 6, 34);
        verify("permute", "Permute1", 5, 20);
        verify("permute", "Permute2", 6, 22);
        verify("permute", "Permute3", 7, 73);
        verify("permute", "Permute4", 6, 41);
        verify("buckets", "Buckets1", 5, 27);
        verify("buckets", "Buckets2", 6, 40);
        verify("buckets", "Buckets3", 7, 73);
        verify("buckets", "Buckets4", 6, 34);
        verify("filled", "Filled1", 5, 18);
        verify("filled", "Filled2", 6, 35);
        verify("filled", "Filled3", 7, 73);
        verify("filled", "Filled4", 6, 40);
        verify("scalar", "Scalar1", 5, 2);
        verify("scalar", "Scalar2", 6, 24);
        verify("scalar", "Scalar3", 7, 126);
        verify("scalar", "Scalar4", 6, 36);
    }
}
