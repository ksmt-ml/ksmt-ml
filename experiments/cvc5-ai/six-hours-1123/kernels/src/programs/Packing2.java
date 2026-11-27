package examples.kernels;
public final class Packing2 {
    public static int run(int x, int y) {
        int a = (x ^ 3) & 63;
        int b = (y + 3) & 63;
        int v = a | (b << 6);
        int a2 = v & 63;
        int b2 = (v >>> 6) & 63;
        if (a != a2 || b != b2) return -1;
        if (v == 0) return 0;
        if (a == b) return 3;
        return a > b ? 1 : 2;
    }
}
