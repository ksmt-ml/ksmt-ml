package examples.kernels;
public final class Carry2 {
    public static int run(int x, int y) {
        int a = (x ^ 44) & 63;
        int b = (y + 44) & 63;
        int p = a + b;
        if (p != (a ^ b) + ((a & b) << 1)) return -1;
        if (p == 0) return 0;
        if (p > 63) return 3;
        return (p & 1) == 0 ? 1 : 2;
    }
}
