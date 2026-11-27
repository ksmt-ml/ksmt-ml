package examples.kernels;
public final class ByteScratch1 {
    public static int run(int x, int y) {
        int a=(x^10)&63, b=(y+10)&63;
        byte[] t=new byte[4]; t[a&3]=(byte)(a+b);
        byte v=t[(a^b)&3]; return v==0 ? 0 : v<0 ? 1 : 2;
    }
}
