package examples.kernels;
public final class Partial4 {
    public static int run(int x, int y) {
        int a = (x ^ 244) & 255;
        int b = (y + 244) & 255;
        int p = a * b;
        int split = (a & 15) * b + (((a >>> 4) * b) << 4);
        if (split != p) return -1;
        if (p == 0) return 0;
        if ((p & 255) > 127) return 3;
        return (p & 1) == 0 ? 1 : 2;
    }
}
