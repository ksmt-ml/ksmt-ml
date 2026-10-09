# Research state

This file records durable research decisions and current evidence. Detailed run
metadata belongs in `experiments/registry.csv`; large outputs belong outside
Git.

## Research question

Can inexpensive, versioned features of an SMT formula predict a cvc5
configuration that improves penalized runtime over the default configuration
and the single best fixed configuration on unseen benchmark families?

## Current milestone

The first public milestone validates the input side of that question:

```text
SMT-LIB -> KSMT-backed feature extraction -> feature table -> basic analysis
```

It does not yet contain a solver runtime matrix or a trained selector.

## Established decisions

1. **Production-oriented extraction.** Structural features come from KSMT ASTs
   so training does not depend on an unrelated Python parser.
2. **Versioned schemas.** `ksmt-machsmt-inspired-v1` is the first frozen local
   contract: 145 unique grammar counts, 11 structural values, and source size.
   Later semantic or ordering changes require a new schema identifier.
3. **Logic-aware modeling.** Theory-specific profiles are preferred over
   feeding every one of the 157 positions to every model.
4. **Family-grouped evaluation.** Related benchmarks from one `(logic, family)`
   group must not cross train/test boundaries.
5. **Simple baselines first.** Default, SBS, and VBS must be measured before a
   learned model. Tree-based regression/classification precedes neural models.
6. **Portable inference later.** ONNX is the intended model handoff format, but
   only after the selector demonstrates value and Python/JVM parity is tested.

## Evidence from the feature-distribution run

Experiment `E0004` re-extracted 64,432 SMT-LIB 2025 benchmarks from ten logic
archives with the frozen `ksmt-machsmt-inspired-v1` contract. Its report is the
evidence source for current counts and distributions. The preceding `v0` run
is retained in the registry as superseded provenance, not silently relabelled.

- 64,341 complete vectors;
- 91 syntax-only fallback vectors;
- zero completely lost files;
- 89.5% of rows come from `QF_FP` and `QF_BVFP`;
- the largest family supplies at least 99% of several logics;
- 27 candidate features are constant and 25 are consequently equal to earlier
  all-zero columns on this sample (there are no repeated schema slots);
- two PCA components explain 19.4% of the selected feature variance.

These observations support per-logic or hierarchical models, grouped splits,
and train-fold-only feature filtering. They do not establish that solver
configuration selection is learnable; that requires a correct runtime matrix
and measurable oracle headroom.

## Next hypotheses

### H1 - Configuration headroom exists

For at least one selected logic, VBS should materially outperform both the cvc5
default and SBS under PAR-2 or PAR-10. If not, the action space must be improved
before model work.

### H2 - Compact profiles are competitive

The logic-aware profiles should match or outperform the full 157-feature
vector after grouped cross-validation because irrelevant theory
clusters add sparsity and leakage opportunities.

### H3 - Family-aware validation changes conclusions

Random file-level splits are expected to overestimate selector quality relative
to `(logic, family)` grouped splits.

## Planned experiment sequence

1. `E0002`: feature fidelity under provisional `v0` (superseded but retained
   as history).
2. `E0003`: corpus extraction and distribution analysis under provisional
   `v0` (superseded but retained as history).
3. `E0004`: frozen-`v1` contract tests and corpus data review.
4. `E0005`: review and freeze a semantically safe cvc5 action catalog using
   development families only.
5. `E0006`: collect the isolated, family-grouped runtime matrix.
6. `E0007`: calculate Default/SBS/VBS and apply the pre-registered ML gate.
7. `E0008`: reproduce the MachSMT empirical-hardness-model baseline only after
   that gate passes.
8. `E0009+`: compare other models, then expand logics, actions, and solvers one
   axis at a time.

## Open questions

- Which cvc5 options create meaningful algorithmic diversity without changing
  semantics or enabling diagnostic/output behavior?
- What wall-clock timeout and repetition policy fit the available hardware?
