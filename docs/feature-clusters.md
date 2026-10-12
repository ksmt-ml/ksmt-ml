# Feature clusters and profiles

The 157-position KSMT-native vector is the input to the first claim-bearing
baseline. The groupings below are descriptive views for diagnostics and named
sensitivity variants: many positions are protocol commands or theory-specific
operators that are constant within a single SMT-LIB logic.

## Clusters

| Cluster | Feature IDs | Use for cvc5 configuration selection |
|---|---:|---|
| Problem commands | 1-4 | Usually drop for single-query data; keep `assert` count |
| Declarations | 5-13 | Keep declarations relevant to the target logic |
| Control commands | 14-31 | Drop for non-incremental benchmarks |
| Quantifiers and bindings | 32-34, 151-154 | Keep only for quantified logics; `let` may still be useful |
| Boolean core | 35-45 | Keep for every logic |
| Arrays | 46-48, 146-147 | Keep for `A` logics |
| Bit-vectors | 49-84, 148-150 | Keep only for `BV` logics |
| Floating point | 85-130 | Keep only for `FP` logics |
| Arithmetic | 131-145 | Keep for integer/real arithmetic logics |
| Uninterpreted functions | 155-156 | Keep for `UF` logics |
| Source metadata | 157 | Keep as a cheap size proxy |

## Initial profiles

`QF_ALIA_BASELINE` contains 36 features:

- assertion, `declare-const`, and `declare-fun` counts (`declare-fun` also
  declares SMT-LIB constants when its argument list is empty);
- `let` frequency;
- Boolean-core frequencies;
- array frequencies and structural statistics;
- integer/real arithmetic frequencies;
- source size.

`QF_AUFLIA_BASELINE` contains 41 features. It extends the ALIA profile with UF
declaration/definition frequencies, average UF arity, and average applications.

These profiles are code-level convenience views over the immutable 157-value
schema, not a completed feature-selection result. The claim-bearing EHM starts
with all 157 values; any compact-profile comparison follows the leakage rules in
[`experiment-protocol.md`](experiment-protocol.md) and is scheduled through
[`PROJECT_PLAN.md`](../PROJECT_PLAN.md).
