package examples.ieee;

public final class WitnessControl {
    public static void main(String[] args) {
        int[] v = {0, 0x80000000, 0x7f800000, 0xff800000, 0x7fc01234, 0x00000001, 0x81000000};
        long[] d = {0L, 0x8000000000000000L, 0x7ff0000000000000L, 0xfff0000000000000L,
                    0x7ff8000000001234L, 0x0000000000000001L, 0x8010000000000000L};
        for (int i = 0; i < v.length; i++) {
            int k = i + 1;
            float f = Float.intBitsToFloat(v[i]);
            double x = Double.longBitsToDouble(d[i]);
            if (Ieee32.fromFloatBits(f) != k || Ieee32.fromBits(v[i]) != k || Ieee32.direct(f) != k
                    || Ieee64.fromDoubleBits(x) != k || Ieee64.fromBits(d[i]) != k || Ieee64.direct(x) != k) {
                throw new IllegalStateException("wrong concrete class " + k);
            }
            System.out.println("WITNESS\t" + k + "\t" + Integer.toHexString(v[i]) + "\t" + Long.toHexString(d[i]));
        }
    }
}
