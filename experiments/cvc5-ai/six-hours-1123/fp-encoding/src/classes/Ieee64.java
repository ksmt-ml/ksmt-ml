package examples.ieee;

public final class Ieee64 {
    public static int fromDoubleBits(double x) {
        return fromBits(Double.doubleToRawLongBits(x));
    }

    public static int fromBits(long v) {
        if (v == 0L) return 1;
        if (v == 0x8000000000000000L) return 2;
        if (v == 0x7ff0000000000000L) return 3;
        if (v == 0xfff0000000000000L) return 4;
        if ((v & 0x7ff0000000000000L) == 0x7ff0000000000000L) return 5;
        if (v < 0) return 7;
        return 6;
    }

    public static int direct(double x) {
        if (x != x) return 5;
        if (x == 0) return 1.0d / x < 0 ? 2 : 1;
        if (x == Double.POSITIVE_INFINITY) return 3;
        if (x == Double.NEGATIVE_INFINITY) return 4;
        if (x < 0) return 7;
        return 6;
    }
}
