package examples.ieee;

public final class Ieee32 {
    public static int fromFloatBits(float x) {
        return fromBits(Float.floatToRawIntBits(x));
    }

    public static int fromBits(int v) {
        if (v == 0) return 1;
        if (v == 0x80000000) return 2;
        if (v == 0x7f800000) return 3;
        if (v == 0xff800000) return 4;
        if ((v & 0x7f800000) == 0x7f800000) return 5;
        if (v < 0) return 7;
        return 6;
    }

    public static int direct(float x) {
        if (x != x) return 5;
        if (x == 0) return 1.0f / x < 0 ? 2 : 1;
        if (x == Float.POSITIVE_INFINITY) return 3;
        if (x == Float.NEGATIVE_INFINITY) return 4;
        if (x < 0) return 7;
        return 6;
    }
}
