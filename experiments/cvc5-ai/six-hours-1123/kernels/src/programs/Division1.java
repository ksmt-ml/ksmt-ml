package examples.kernels;
public final class Division1 {
    public static int run(int x, int y) {
        int a = (x ^ 33) & 255;
        int b = (y + 33) & 255;
        int d = b | 1;
        int q = a / d;
        int r = a % d;
        if (q * d + r != a || r >= d) return -1;
        if (q == 0) return 0;
        if (r == 0) return 1;
        return (r & 8) == 0 ? 2 : 3;
    }
}
