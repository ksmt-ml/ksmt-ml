package examples.kernels;
public final class Copy4 {
    public static int run(int x, int y) {
        int a = (x ^ 34) & 63;
        int b = (y + 34) & 63;
        int i = a & 3, j = b & 3;
        int[] data = new int[4];
        int[] copy = new int[4];
        data[i] = (a & 15) + 1;
        data[j] = (b & 15) + 1;
        for (int k = 0; k < 4; k++) copy[k] = data[k];
        if (copy[i] != data[i] || copy[j] != data[j]) return -1;
        if (i == j) return 0;
        return copy[i] == copy[j] ? 1 : (copy[i] < copy[j] ? 2 : 3);
    }
}
