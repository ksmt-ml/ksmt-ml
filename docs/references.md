# References and provenance

## Primary research references

- Joseph Scott, Aina Niemetz, Mathias Preiner, Saeed Nejati, and Vijay Ganesh,
  “MachSMT: A Machine Learning-based Algorithm Selector for SMT Solvers,”
  TACAS 2021 ([author PDF](https://www-cs.stanford.edu/~niemetz/publications/2021/ScottNiemetzPreinerNejatiGanesh-TACAS21.pdf)).
- Joseph Scott, Aina Niemetz, Mathias Preiner, Saeed Nejati, and Vijay Ganesh,
  “Algorithm selection for SMT,” *International Journal on Software Tools for
  Technology Transfer* 25, 219-239 (2023)
  ([author PDF](https://cs.stanford.edu/~niemetz/publications/2023/ScottNPNG-STTT23.pdf),
  [DOI](https://doi.org/10.1007/s10009-023-00696-0)).
- [MachSMT repository](https://github.com/MachSMT/MachSMT).
- [MachSMT TACAS 2021 artifact](https://github.com/MachSMT/TACAS-Artifact).

## Exact historical source consulted

The pre-string token order was inspected at MachSMT commit `1b6a118`:

- [`machsmt/smtlib/util.py`](https://github.com/MachSMT/MachSMT/blob/1b6a118/machsmt/smtlib/util.py)
- [`machsmt/benchmark/benchmark.py`](https://github.com/MachSMT/MachSMT/blob/1b6a118/machsmt/benchmark/benchmark.py)
- [`machsmt/features/arrays.py`](https://github.com/MachSMT/MachSMT/blob/1b6a118/machsmt/features/arrays.py)
- [`machsmt/features/bv.py`](https://github.com/MachSMT/MachSMT/blob/1b6a118/machsmt/features/bv.py)
- [`machsmt/features/quantifiers.py`](https://github.com/MachSMT/MachSMT/blob/1b6a118/machsmt/features/quantifiers.py)
- [`machsmt/features/uf.py`](https://github.com/MachSMT/MachSMT/blob/1b6a118/machsmt/features/uf.py)

This matters because neither “162 from the TACAS table” nor “the public
TACAS-21 branch” uniquely identifies one executable vector. The TACAS table has
range inconsistencies; the corrected STTT table includes later string
features; the historical Python list contains duplicate `get-*` entries; and
the historical structural implementation does not exactly match every statistic
named by the paper.

The local schema therefore uses a new name and defines its own testable
semantics. Its `v1` grammar block keeps the 145 unique non-string constructs
and removes the three repeated historical entries. No MachSMT source files,
datasets, or trained models are vendored.

## Baseline-model provenance

The first learned baseline follows the MachSMT empirical hardness model (EHM):
zero-mean/unit-variance scaling, degree-two polynomial interactions, PCA to 35
components, and one AdaBoost PAR-2-score regressor per solver action with 200
decision-tree estimators and linear loss. Selection takes the action with the
lowest prediction. The papers also describe pairwise classifiers, but those are
a later comparison rather than the first baseline here.

MachSMT evaluated with random five-fold splits within divisions. This project
changes that evaluation protocol deliberately: all preprocessing is fit inside
nested family-grouped folds and the final result is measured once on sealed
families. Thus the model is paper-derived, while the validation contract is
stricter and targets out-of-family generalization.

## Dataset and runtime dependencies

- [SMT-LIB 2025 benchmark release](https://zenodo.org/records/16740866),
  version 2025.08.04.
- [KSMT](https://github.com/UnitTestBot/ksmt), version 0.6.7.
- [cvc5](https://github.com/cvc5/cvc5), version 1.3.4 through the current KSMT
  dependency.

The Kotlin extractor, scanner, schema, golden oracle, dataset pipeline, and
tests are original work in this repository. “MachSMT-inspired” describes
research provenance and does not imply affiliation, endorsement, or binary
compatibility.
