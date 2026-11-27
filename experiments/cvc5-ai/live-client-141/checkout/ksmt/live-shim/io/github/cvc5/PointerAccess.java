package io.github.cvc5;

/**
 * Shim for cvc5 1.4.x: AbstractPointer and IPointer are package-private there,
 * but KSMT 0.6.7 (KCvc5TermManager) needs getPointer() and deletePointer() on every native handle.
 * This class lives in the same package, so it can reach them.
 */
public final class PointerAccess {
    private PointerAccess() {}

    public static long pointer(Object o) {
        return ((AbstractPointer) o).getPointer();
    }

    public static void delete(Object o) {
        ((AbstractPointer) o).deletePointer();
    }
}
