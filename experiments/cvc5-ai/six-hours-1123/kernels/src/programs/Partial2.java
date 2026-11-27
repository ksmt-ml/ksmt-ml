package examples.kernels;
public final class Partial2 {
    public static int run(int x, int y) {
        int a = (x ^ 33) & 63;
        int b = (y + 33) & 63;
        int p = a * b;
        int split = (a & 7) * b + (((a >>> 3) * b) << 3);
        if (split != p) return -1;
        if (p == 0) return 0;
        if ((p & 63) > 31) return 3;
        return (p & 1) == 0 ? 1 : 2;
    }
}
