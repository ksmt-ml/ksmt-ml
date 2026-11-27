package examples.kernels;
public final class Signed4 {
    public static int run(int x, int y) {
        int a = (x ^ 110) & 255;
        int b = (y + 110) & 255;
        int n = ((a << 24) >> 24);
        int q = a < 128 ? a : a - 256;
        if (n != q) return -1;
        if (n == 0) return 0;
        if (b == 0) return 1;
        return n > b ? 2 : n < 0 ? 3 : 4;
    }
}
