package examples.kernels;
public final class SlidingSlot2 {
    public static int run(int x, int y) {
        int a=(x^56)&127, b=(y+56)&127;
        int[] t=new int[] {a,b,0,0}; int i=b&1; t[i+2]=t[i]; t[i]=0;
        if(t[i+2] != (i==0 ? a : b)) return -1; return t[a&3]==0 ? 0 : t[a&3]>16 ? 1 : 2;
    }
}
