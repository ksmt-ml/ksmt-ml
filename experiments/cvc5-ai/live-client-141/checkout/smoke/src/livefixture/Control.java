package livefixture;

public final class Control {
    public static int scalar(int x) {
        if (x < 0) return -11;
        if (x == 0) return 22;
        if (x > 0) return 33;
        return 999; // infeasible
    }

    public static int array(int[] a, int i) {
        if (a == null || i < 0 || i >= a.length) return -1;
        return a[i] == 41 ? 41 : 7;
    }

    public static int floating(float x) {
        if (x != x) return -1;
        return x > 0 ? 5 : 0;
    }
}
