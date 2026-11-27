package examples.kernels;
public final class CounterRead1 {
    public static int run(int x, int y) {
        int a=(x^42)&63, b=(y+42)&63;
        int[] t=new int[8]; int i=b&7;
        t[i] += a&7; t[(i+2)&7] += b&7;
        if(t[i]!= (a&7)) return -1; return t[(a+b)&7] == 0 ? 0 : 1;
    }
}
