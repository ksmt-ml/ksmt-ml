package examples.kernels;
public final class Buckets2 {
    public static int run(int x, int y) {
        int a = (x ^ 40) & 63;
        int b = (y + 40) & 63;
        int[] data = new int[4];
        int i = (a ^ (a >>> 3)) & 3, j = (b ^ (b >>> 3)) & 3;
        data[i] += a & 7;
        data[j] += b & 7;
        if (data[0] + data[1] + data[2] + data[3] != (a & 7) + (b & 7)) return -1;
        if (data[i] == 0) return 0;
        if (i == j) return data[i] > 7 ? 1 : 2;
        return data[i] > data[j] ? 3 : 4;
    }
}
