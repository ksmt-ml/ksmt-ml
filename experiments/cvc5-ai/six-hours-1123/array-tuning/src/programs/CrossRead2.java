package examples.kernels;
public final class CrossRead2 {
    public static int run(int x, int y) {
        int a=(x^59)&127, b=(y+59)&127;
        int[] t=new int[4], u=new int[4]; int i=a&3, j=b&3;
        t[i]=a+1; u[j]=b+1; t[j]+=u[i];
        if(t[i]==0 || u[j]==0) return -1; return t[j]==0 ? 0 : t[j]>16 ? 1 : 2;
    }
}
