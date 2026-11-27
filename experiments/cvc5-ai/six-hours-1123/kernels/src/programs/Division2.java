package examples.kernels;
public final class Division2 {
    public static int run(int x, int y) {
        int a = (x ^ 36) & 63;
        int b = (y + 36) & 63;
        int d = b | 1;
        int q = a / d;
        int r = a % d;
        if (q * d + r != a || r >= d) return -1;
        if (q == 0) return 0;
        if (r == 0) return 1;
        return (r & 4) == 0 ? 2 : 3;
    }
}
