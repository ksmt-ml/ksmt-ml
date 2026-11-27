package examples.kernels;
public final class Carry1 {
    public static int run(int x, int y) {
        int a = (x ^ 193) & 255;
        int b = (y + 193) & 255;
        int p = a + b;
        if (p != (a ^ b) + ((a & b) << 1)) return -1;
        if (p == 0) return 0;
        if (p > 255) return 3;
        return (p & 1) == 0 ? 1 : 2;
    }
}
