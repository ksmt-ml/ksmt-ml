package examples.kernels;
public final class WidthControl1 {
    public static int run(int x, int y) {
        int a=(x^14)&63, b=(y+14)&63;
        int v=((a&3)==(b&3)) ? a+1 : 0;
        if(v==0) return 0; return v>16 ? 1 : 2;
    }
}
