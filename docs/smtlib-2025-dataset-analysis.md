# SMT-LIB 2025 ten-archive sample

Schema: `ksmt-machsmt-inspired-v1`, the frozen 157-position local contract.
This report is experiment `E0004`; the older `v0` run remains in the registry
only as superseded provenance.

Source: [Zenodo record 16740866](https://zenodo.org/records/16740866), version
2025.08.04. The release contains non-incremental benchmarks grouped into one
archive per SMT-LIB logic.

An archive is not one solver input. Each `.smt2` file inside it is an independent
benchmark. A file can contain several `assert` commands; for a non-incremental
benchmark they collectively form one solver query.

## Selected archives

| Logic | Benchmarks | Main fragment |
|---|---:|---|
| `QF_ALIA` | 176 | Quantifier-free arrays and linear integer arithmetic |
| `QF_AUFBV` | 75 | Quantifier-free arrays, UF, and bit-vectors |
| `QF_AUFLIA` | 1,303 | Quantifier-free arrays, UF, and linear integer arithmetic |
| `QF_AX` | 551 | Quantifier-free extensional arrays |
| `QF_BVFP` | 17,250 | Quantifier-free bit-vectors and floating point |
| `QF_FP` | 40,406 | Quantifier-free floating point |
| `QF_FPLRA` | 59 | Quantifier-free floating point and linear real arithmetic |
| `LIA` | 536 | Linear integer arithmetic, quantifiers allowed |
| `NIA` | 257 | Non-linear integer arithmetic, quantifiers allowed |
| `NRA` | 3,819 | Non-linear real arithmetic, quantifiers allowed |

Total: 64,432 benchmarks.

The `QF` prefix means quantifier-free. Other common components are `A`/`AX`
for arrays, `UF` for free sorts and uninterpreted functions, `BV` for
fixed-size bit-vectors, `FP` for floating point, `IA`/`RA` for integer/real
arithmetic, and `L`/`N` for linear/non-linear arithmetic.

## Extraction result

- 64,341 complete feature vectors.
- 91 syntax-only rows after the safe AST traversal cap was reached.
- 0 files lost completely.
- Expected statuses: 35,440 `sat`, 28,532 `unsat`, 460 `unknown`.

Syntax-only rows preserve SMT-LIB token counts and source size. AST-derived
features 146-156 are missing (`NaN`), not zero.

## Main findings

1. `QF_FP` and `QF_BVFP` contribute 89.5% of all rows. Global training needs
   logic-aware weighting or sampling.
2. Family concentration is severe: the largest family supplies 99.0% of
   `QF_FP`, 99.5% of `QF_BVFP`, and 99.8% of `NRA`. Cross-validation must group
   by `(logic, family)`.
3. Theory-specific clusters visibly separate logics, but the first two PCA
   components explain only 19.4% of variance. The geometry is not adequately
   represented by a simple two-dimensional linear projection.
4. Counts and source size are heavy-tailed. Use `log1p`, robust scaling, or
   tree-based models.
5. After excluding the 18 control-command columns, 27 of 139 candidates are
   constant and 25 are therefore also exact duplicates of earlier all-zero
   columns on this particular corpus. Their union leaves 112 mechanically
   usable features. These are data-dependent coincidences, not repeated schema
   slots; filtering must be fitted only on training folds.
6. The current ten-archive sample is good for validating feature extraction,
   but it is not yet a solver-selection training dataset. Runtime labels for
   concrete cvc5 configurations are still required.

## Pilot cohort decision

The first claim-bearing runtime study uses `QF_ALIA`, `QF_AUFLIA`, and `LIA`.
They have 6, 7, and 5 families respectively; their largest-family shares are
29.5%, 49.7%, and 35.3%. This is enough to design family-level development and
holdout splits, though uncertainty must still be reported at family level.

| Logic | Rows | Full AST | Syntax-only | SAT | UNSAT | UNKNOWN |
|---|---:|---:|---:|---:|---:|---:|
| `QF_ALIA` | 176 | 170 | 6 | 54 | 72 | 50 |
| `QF_AUFLIA` | 1,303 | 1,279 | 24 | 543 | 760 | 0 |
| `LIA` | 536 | 536 | 0 | 270 | 266 | 0 |

There are no exact-content duplicate hashes within this 2,015-row pilot. The
ten-archive corpus as a whole has three duplicate-hash pairs (six rows), which
must be co-located or deduplicated before any broader split. Hash equality does
not rule out renamed or lightly transformed near-duplicates. The reviewed
pilot split therefore remains an explicit gate in
[`PROJECT_PLAN.md`](../PROJECT_PLAN.md).

The other sampled logics remain descriptive or stress-test cohorts until their
family coverage improves. In particular, the largest family contributes 99.0%
of `QF_FP`, 99.5% of `QF_BVFP`, and 99.8% of `NRA`, so their file counts must
not be mistaken for independent evidence.
