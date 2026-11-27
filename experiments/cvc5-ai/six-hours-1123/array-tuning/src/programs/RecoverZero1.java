package examples.kernels;
public final class RecoverZero1 {
    public static int run(int x, int y) {
        int a=(x^23)&63, b=(y+23)&63;
        int[] t=new int[4]; int i=a&3; t[i]=(a^b)+1;
        if(t[(i+1)&3]!=0) return -1; return t[i]>16 ? 1 : 2;
    }
}
