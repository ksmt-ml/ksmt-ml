package examples.kernels;
public final class Packing3 {
    public static int run(int x, int y) {
        int a = (x ^ 32) & 127;
        int b = (y + 32) & 127;
        int v = a | (b << 7);
        int a2 = v & 127;
        int b2 = (v >>> 7) & 127;
        if (a != a2 || b != b2) return -1;
        if (v == 0) return 0;
        if (a == b) return 3;
        return a > b ? 1 : 2;
    }
}
