package examples.kernels;
public final class ElementSwap2 {
    public static int run(int x, int y) {
        int a=(x^1)&127, b=(y+1)&127;
        int[] t=new int[2]; t[0]=a+1; t[1]=b+1;
        int i=a&1,j=b&1,v=t[i]; t[i]=t[j]; t[j]=v;
        if(t[0]+t[1]!=a+b+2) return -1; return i==j ? 0 : t[0]>t[1] ? 1 : 2;
    }
}
