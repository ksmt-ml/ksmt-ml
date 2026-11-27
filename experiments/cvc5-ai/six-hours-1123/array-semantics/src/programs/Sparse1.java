package examples.kernels;
public final class Sparse1 {
    public static int run(int x, int y) {
        int a = (x ^ 2) & 31;
        int b = (y + 2) & 31;
        int i = a & 3;
        int[] data = new int[4];
        data[i] = 1 + (b & 7);
        int here = data[b & 3];
        if (here == 0) return 0;
        if (here != 1 + (b & 7)) return -1;
        return (a & 8) == 0 ? 1 : 2;
    }
}
