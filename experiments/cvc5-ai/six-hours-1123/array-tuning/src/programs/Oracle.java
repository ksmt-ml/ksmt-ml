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
        verify("SingleWrite", "SingleWrite1", 6, 1);
        verify("SingleWrite", "SingleWrite2", 7, 95);
        verify("ElementSwap", "ElementSwap1", 6, 2);
        verify("ElementSwap", "ElementSwap2", 7, 1);
        verify("RecoverZero", "RecoverZero1", 6, 23);
        verify("RecoverZero", "RecoverZero2", 7, 105);
        verify("ByteScratch", "ByteScratch1", 6, 10);
        verify("ByteScratch", "ByteScratch2", 7, 67);
        verify("CounterRead", "CounterRead1", 6, 42);
        verify("CounterRead", "CounterRead2", 7, 94);
        verify("Bitset", "Bitset1", 6, 51);
        verify("Bitset", "Bitset2", 7, 49);
        verify("MirrorRead", "MirrorRead1", 6, 60);
        verify("MirrorRead", "MirrorRead2", 7, 55);
        verify("WidthControl", "WidthControl1", 6, 14);
        verify("WidthControl", "WidthControl2", 7, 72);
        verify("SlidingSlot", "SlidingSlot1", 6, 46);
        verify("SlidingSlot", "SlidingSlot2", 7, 56);
        verify("CrossRead", "CrossRead1", 6, 35);
        verify("CrossRead", "CrossRead2", 7, 59);
        verify("BucketDelete", "BucketDelete1", 6, 7);
        verify("BucketDelete", "BucketDelete2", 7, 69);
        verify("LongAccum", "LongAccum1", 6, 38);
        verify("LongAccum", "LongAccum2", 7, 98);
    }
}
