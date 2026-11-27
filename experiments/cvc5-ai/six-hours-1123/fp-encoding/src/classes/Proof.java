package examples.ieee;

import java.util.TreeSet;

public final class Proof {
    public static void main(String[] args) {
        float[] fv = {0.0f, -0.0f, Float.POSITIVE_INFINITY, Float.NEGATIVE_INFINITY, Float.NaN, 2.0f, -2.0f};
        double[] dv = {0.0, -0.0, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NaN, 2.0, -2.0};
        TreeSet<Integer> a = new TreeSet<>(), b = new TreeSet<>();
        for (float x : fv) {
            int y = Scaled32.rawNegated(x);
            if (y != Scaled32.directNegated(x)) throw new AssertionError(x);
            a.add(y);
        }
        for (double x : dv) {
            int y = Scaled64.rawScaled(x);
            if (y != Scaled64.directScaled(x)) throw new AssertionError(x);
            b.add(y);
        }
        if (!a.toString().equals("[1, 2, 3, 4, 5, 6, 7]") || !a.equals(b)) throw new AssertionError();
        for (int i = 0; i < 1024; i++) {
            if (Payload.quiet32(i) != 1 || Payload.quiet64(i) != 1) throw new AssertionError(i);
        }
        System.out.println("category-control 1,2,3,4,5,6,7");
        System.out.println("payload-control 1");
    }
}
