package examples.kernels;
public final class LongAccum2 {
    public static int run(int x, int y) {
        int a=(x^98)&127, b=(y+98)&127;
        long[] t=new long[4]; int i=a&3,j=b&3;
        t[i]=1L<< (a&31); t[j]+=1L<<(b&31);
        long value=t[(a+b)&3]; return value==0 ? 0 : value>(1L<<16) ? 1 : 2;
    }
}
