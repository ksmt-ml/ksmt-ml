package examples.kernels;
public final class Packing1 {
    public static int run(int x, int y) {
        int a = (x ^ 246) & 255;
        int b = (y + 246) & 255;
        int v = a | (b << 8);
        int a2 = v & 255;
        int b2 = (v >>> 8) & 255;
        if (a != a2 || b != b2) return -1;
        if (v == 0) return 0;
        if (a == b) return 3;
        return a > b ? 1 : 2;
    }
}
