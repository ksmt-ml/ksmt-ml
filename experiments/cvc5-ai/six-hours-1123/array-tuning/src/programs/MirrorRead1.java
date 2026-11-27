package examples.kernels;
public final class MirrorRead1 {
    public static int run(int x, int y) {
        int a=(x^60)&63, b=(y+60)&63;
        int[] t=new int[8]; int i=a&3; t[i]=a+1; t[7-i]=b+1;
        if(t[7-i]!=b+1) return -1; return t[b&7]==0 ? 0 : t[b&7]>16 ? 1 : 2;
    }
}
