package examples.kernels;
public final class Filled1 {
    public static int run(int x, int y) {
        int a = (x ^ 18) & 31;
        int b = (y + 18) & 31;
        int[] data = new int[] {a, b, a ^ b, a + b};
        int i = (a ^ b) & 3, j = (i + 2) & 3;
        data[i] = data[j] + 1;
        if (data[i] != data[j] + 1) return -1;
        if (data[j] == 0) return 0;
        return (data[i] & 1) == 0 ? 1 : 2;
    }
}
