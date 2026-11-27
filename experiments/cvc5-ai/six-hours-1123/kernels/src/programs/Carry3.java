package examples.kernels;
public final class Carry3 {
    public static int run(int x, int y) {
        int a = (x ^ 91) & 127;
        int b = (y + 91) & 127;
        int p = a + b;
        if (p != (a ^ b) + ((a & b) << 1)) return -1;
        if (p == 0) return 0;
        if (p > 127) return 3;
        return (p & 1) == 0 ? 1 : 2;
    }
}
