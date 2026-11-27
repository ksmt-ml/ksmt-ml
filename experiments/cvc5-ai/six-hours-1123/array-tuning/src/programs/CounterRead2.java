package examples.kernels;
public final class CounterRead2 {
    public static int run(int x, int y) {
        int a=(x^94)&127, b=(y+94)&127;
        int[] t=new int[8]; int i=b&7;
        t[i] += a&7; t[(i+2)&7] += b&7;
        if(t[i]!= (a&7)) return -1; return t[(a+b)&7] == 0 ? 0 : 1;
    }
}
