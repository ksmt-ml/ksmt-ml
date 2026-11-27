package examples.ieee;

public final class Payload {
    public static int quiet32(int x) {
        int bits = 0x7fc00000 | (x & 1023);
        float f = Float.intBitsToFloat(bits);
        return Float.floatToRawIntBits(f) == bits ? 1 : -1;
    }

    public static int quiet64(int x) {
        long bits = 0x7ff8000000000000L | (x & 1023);
        double f = Double.longBitsToDouble(bits);
        return Double.doubleToRawLongBits(f) == bits ? 1 : -1;
    }
}
