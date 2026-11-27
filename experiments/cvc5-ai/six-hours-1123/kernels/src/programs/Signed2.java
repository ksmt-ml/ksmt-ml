package examples.kernels;
public final class Signed2 {
    public static int run(int x, int y) {
        int a = (x ^ 52) & 63;
        int b = (y + 52) & 63;
        int n = ((a << 26) >> 26);
        int q = a < 32 ? a : a - 64;
        if (n != q) return -1;
        if (n == 0) return 0;
        if (b == 0) return 1;
        return n > b ? 2 : n < 0 ? 3 : 4;
    }
}
