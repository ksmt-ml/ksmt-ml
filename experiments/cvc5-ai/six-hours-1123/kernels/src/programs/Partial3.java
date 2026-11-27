package examples.kernels;
public final class Partial3 {
    public static int run(int x, int y) {
        int a = (x ^ 64) & 127;
        int b = (y + 64) & 127;
        int p = a * b;
        int split = (a & 7) * b + (((a >>> 3) * b) << 3);
        if (split != p) return -1;
        if (p == 0) return 0;
        if ((p & 127) > 63) return 3;
        return (p & 1) == 0 ? 1 : 2;
    }
}
