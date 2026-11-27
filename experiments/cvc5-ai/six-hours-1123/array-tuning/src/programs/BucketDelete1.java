package examples.kernels;
public final class BucketDelete1 {
    public static int run(int x, int y) {
        int a=(x^7)&63, b=(y+7)&63;
        int[] t=new int[4]; t[a&3]=a+1; t[b&3]=0;
        if(t[b&3]!=0) return -1; return t[a&3]==0 ? 0 : t[a&3]>16 ? 1 : 2;
    }
}
