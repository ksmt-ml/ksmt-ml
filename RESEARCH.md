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
The committed ONNX policy is an infrastructure interface fixture with
hand-authored weights, not an experiment result or learned baseline.

## Established decisions

1. **Production-oriented extraction.** Structural features come from KSMT ASTs
   so training does not depend on an unrelated Python parser.
2. **Versioned schemas.** `ksmt-machsmt-inspired-v1` is the first frozen local
   contract: 145 unique grammar counts, 11 structural values, and source size.
   Later semantic or ordering changes require a new schema identifier.
3. **Paper-derived baseline first.** The first EHM uses the complete 157-value
   vector. Per-logic models and compact theory-specific profiles are evaluated
   as named variants, not silently substituted into the baseline.
4. **Family-grouped evaluation.** Related benchmarks from one `(logic, family)`
   group must not cross train/test boundaries.
5. **Simple baselines first.** Default, SBS, and VBS must be measured before a
   learned model. Tree-based regression/classification precedes neural models.
6. **Portable inference boundary.** ONNX is the model handoff format. The
   committed fixture validates the interface only; a trained bundle is eligible
   for deployment only after performance evidence and Python/JVM parity.

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

## Plan ownership

Milestones, future experiments, engineering hardening, and open decisions are
maintained only in [`PROJECT_PLAN.md`](PROJECT_PLAN.md). The machine-readable
status of individual runs remains in `experiments/registry.csv`.
