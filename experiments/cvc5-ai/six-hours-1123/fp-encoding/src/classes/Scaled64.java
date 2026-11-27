package examples.ieee;

public final class Scaled64 {
    public static int rawScaled(double x) {
        return Ieee64.fromBits(Double.doubleToRawLongBits(x / -2.0));
    }

    public static int directScaled(double x) {
        return Ieee64.direct(x / -2.0);
    }
}
