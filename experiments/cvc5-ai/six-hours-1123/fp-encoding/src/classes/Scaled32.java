package examples.ieee;

public final class Scaled32 {
    public static int rawNegated(float x) {
        return Ieee32.fromBits(Float.floatToRawIntBits(-x));
    }

    public static int directNegated(float x) {
        return Ieee32.direct(-x);
    }
}
