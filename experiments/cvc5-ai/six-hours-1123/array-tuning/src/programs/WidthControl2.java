package examples.kernels;
public final class WidthControl2 {
    public static int run(int x, int y) {
        int a=(x^72)&127, b=(y+72)&127;
        int v=((a&3)==(b&3)) ? a+1 : 0;
        if(v==0) return 0; return v>16 ? 1 : 2;
    }
}
