package examples.kernels;
public final class SingleWrite1 {
    public static int run(int x, int y) {
        int a=(x^1)&63, b=(y+1)&63;
        int[] t=new int[8]; t[a&7]=1+(a&7);
        int value=t[b&7]; return value==0 ? 0 : value>4 ? 1 : 2;
    }
}
