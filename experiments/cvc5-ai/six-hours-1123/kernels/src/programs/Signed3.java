package examples.kernels;
public final class Signed3 {
    public static int run(int x, int y) {
        int a = (x ^ 88) & 127;
        int b = (y + 88) & 127;
        int n = ((a << 25) >> 25);
        int q = a < 64 ? a : a - 128;
        if (n != q) return -1;
        if (n == 0) return 0;
        if (b == 0) return 1;
        return n > b ? 2 : n < 0 ? 3 : 4;
    }
}
