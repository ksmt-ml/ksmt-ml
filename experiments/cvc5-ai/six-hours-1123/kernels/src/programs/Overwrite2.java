package examples.kernels;
public final class Overwrite2 {
    public static int run(int x, int y) {
        int a = (x ^ 54) & 63;
        int b = (y + 54) & 63;
        int i = a & 3, j = b & 3;
        int[] table = new int[4];
        table[i] = a + 1;
        table[j] = b + 1;
        if (table[j] != b + 1) return -1;
        if (i == j) {
            if (table[i] != b + 1) return -1;
            return (a & 4) == 0 ? 0 : 1;
        }
        if (table[i] != a + 1) return -1;
        return (b & 4) == 0 ? 2 : 3;
    }
}
