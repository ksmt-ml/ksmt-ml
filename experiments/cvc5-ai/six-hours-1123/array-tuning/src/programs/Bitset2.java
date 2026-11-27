package examples.kernels;
public final class Bitset2 {
    public static int run(int x, int y) {
        int a=(x^49)&127, b=(y+49)&127;
        boolean[] t=new boolean[4]; t[a&3]=true; t[b&3]=true;
        if(!t[a&3] || !t[b&3]) return -1; return t[(a^b)&3] ? 1 : 0;
    }
}
