package examples.kernels;
public final class ByteScratch2 {
    public static int run(int x, int y) {
        int a=(x^67)&127, b=(y+67)&127;
        byte[] t=new byte[4]; t[a&3]=(byte)(a+b);
        byte v=t[(a^b)&3]; return v==0 ? 0 : v<0 ? 1 : 2;
    }
}
