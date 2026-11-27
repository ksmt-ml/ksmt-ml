package examples.kernels;
public final class SingleWrite2 {
    public static int run(int x, int y) {
        int a=(x^95)&127, b=(y+95)&127;
        int[] t=new int[8]; t[a&7]=1+(a&7);
        int value=t[b&7]; return value==0 ? 0 : value>4 ? 1 : 2;
    }
}
