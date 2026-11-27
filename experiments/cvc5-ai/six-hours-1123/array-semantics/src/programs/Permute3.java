package examples.kernels;
public final class Permute3 {
    public static int run(int x, int y) {
        int a = (x ^ 73) & 127;
        int b = (y + 73) & 127;
        int i = a & 3, j = (a + 1) & 3;
        int[] data = new int[4];
        data[i] = a + 1;
        data[j] = b + 1;
        int k = b & 3;
        if (k == i) return data[k] == a + 1 ? 0 : -1;
        if (k == j) return data[k] == b + 1 ? 1 : -1;
        return data[k] == 0 ? 2 : -1;
    }
}
