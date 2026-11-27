package examples.kernels;
public final class Scalar3 {
    public static int run(int x, int y) {
        int a = (x ^ 126) & 127;
        int b = (y + 126) & 127;
        int i = a & 3, j = b & 3;
        int here = i == j ? (b & 7) + 1 : 0;
        if (here == 0) return 0;
        if (here != (b & 7) + 1) return -1;
        return (a & 8) == 0 ? 1 : 2;
    }
}
