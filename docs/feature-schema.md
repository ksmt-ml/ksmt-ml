# Feature schema `ksmt-machsmt-inspired-v1`

## Status and compatibility

`v1` is the first frozen KSMT-native schema. It contains 157 ordered `double`
values:

```text
145 unique grammar counts + 11 structural values + 1 source-size value = 157
```

The design is inspired by MachSMT but is not binary-compatible with a
historical MachSMT extractor or model. Ordering or semantic changes require a
new schema identifier.

## Why the grammar block has 145 values

The TACAS 2021 feature table labels a 162-dimensional design, but its ranges
contain two numbering errors. The corrected STTT 2023 table assigns IDs 1-148
to non-string grammar positions, 149-184 to strings, 185-195 to structural
features, and 196 to source size.

The historical pre-string token list has 148 entries only because three
`get-*` tokens occur twice: `get-assignment`, `get-info`, and `get-option`.
Those repetitions do not represent different SMT-LIB constructs. `v1` removes
them rather than preserving duplicate columns, leaving 145 unique non-string
grammar features. String/regular-expression features are outside the current
project scope.

## Ordered groups

| IDs | Count | Meaning | Source |
|---:|---:|---|---|
| 1-4 | 4 | problem commands | SMT-LIB tokens |
| 5-13 | 9 | declarations and definitions | SMT-LIB tokens |
| 14-31 | 18 | control and metadata commands | SMT-LIB tokens |
| 32-34 | 3 | `exists`, `forall`, `let` | SMT-LIB tokens |
| 35-45 | 11 | Boolean core | SMT-LIB tokens |
| 46-48 | 3 | arrays | SMT-LIB tokens |
| 49-84 | 36 | bit-vectors | SMT-LIB tokens |
| 85-130 | 46 | floating point | SMT-LIB tokens |
| 131-145 | 15 | integer and real arithmetic | SMT-LIB tokens |
| 146-147 | 2 | array structure | KSMT AST |
| 148-150 | 3 | BV-adder-chain statistics | KSMT AST |
| 151-154 | 4 | quantifier statistics | KSMT AST |
| 155-156 | 2 | applied-UF statistics | KSMT AST |
| 157 | 1 | UTF-8 source size in bytes | source metadata |

## Grammar counts (1-145)

The lexer counts exact SMT-LIB tokens while excluding comments, string
contents, and quoted-symbol contents. It does not normalize equivalent syntax.
Within each row below, IDs increase from left to right.

| IDs | Ordered SMT-LIB tokens |
|---:|---|
| 1-4 | `as`, `assert`, `check-sat`, `check-sat-assuming` |
| 5-13 | `declare-const`, `declare-datatype`, `declare-datatypes`, `declare-fun`, `declare-sort`, `define-fun`, `define-fun-rec`, `define-funs-rec`, `define-sort` |
| 14-31 | `echo`, `exit`, `get-assertions`, `get-assignment`, `get-info`, `get-model`, `get-option`, `get-proof`, `get-unsat-assumptions`, `get-unsat-core`, `get-value`, `pop`, `push`, `reset`, `reset-assertions`, `set-info`, `set-logic`, `set-option` |
| 32-34 | `exists`, `forall`, `let` |
| 35-45 | `true`, `false`, `not`, `=>`, `and`, `or`, `xor`, `=`, `distinct`, `ite`, `Bool` |
| 46-48 | `Array`, `select`, `store` |
| 49-84 | `BitVec`, `concat`, `extract`, `bvnot`, `bvand`, `bvor`, `bvneg`, `bvadd`, `bvmul`, `bvudiv`, `bvurem`, `bvshl`, `bvlshr`, `bvult`, `bvnand`, `bvnor`, `bvxor`, `bvxnor`, `bvcomp`, `bvsub`, `bvsdiv`, `bvsrem`, `bvsmod`, `bvashr`, `repeat`, `zero_extend`, `sign_extend`, `rotate_left`, `rotate_right`, `bvule`, `bvugt`, `bvuge`, `bvslt`, `bvsle`, `bvsgt`, `bvsge` |
| 85-130 | `RoundingMode`, `FloatingPoint`, `Float16`, `Float32`, `Float64`, `Float128`, `fp`, `roundNearestTiesToEven`, `roundNearestTiesToAway`, `roundTowardPositive`, `roundTowardNegative`, `roundTowardZero`, `RNE`, `RNA`, `RTP`, `RTN`, `RTZ`, `fp.abs`, `fp.neg`, `fp.add`, `fp.sub`, `fp.mul`, `fp.div`, `fp.fma`, `fp.sqrt`, `fp.rem`, `fp.roundToIntegral`, `fp.min`, `fp.max`, `fp.leq`, `fp.lt`, `fp.geq`, `fp.gt`, `fp.eq`, `fp.isNormal`, `fp.isSubnormal`, `fp.isZero`, `fp.isInfinite`, `fp.isNaN`, `fp.isNegative`, `fp.isPositive`, `to_fp`, `to_fp_unsigned`, `fp.to_ubv`, `fp.to_sbv`, `fp.to_real` |
| 131-145 | `Int`, `-`, `+`, `*`, `div`, `mod`, `abs`, `<=`, `<`, `>=`, `>`, `to_real`, `to_int`, `is_int`, `Real` |

## Structural values (146-156)

| ID | Name | Exact semantics |
|---:|---|---|
| 146 | average selects per referenced array expression | Mean number of `select` applications grouped by the KSMT array expression used as their array argument |
| 147 | average maximal store-chain depth | Mean number of `store` nodes in each maximal chain; isolated one-node chains are excluded |
| 148 | BV-adder-chain mean | Mean number of `bvadd` nodes in each maximal chain; isolated one-node chains are excluded |
| 149 | BV-adder-chain median | Median over the same maximal-chain sizes |
| 150 | BV-adder-chain population deviation | Population standard deviation over the same sizes |
| 151 | forall-bound variables | Total variables bound by universal quantifiers |
| 152 | exists-bound variables | Total variables bound by existential quantifiers |
| 153 | exists/forall ratio | ID 152 divided by ID 151; zero when ID 151 is zero |
| 154 | average quantifier nesting | Mean one-based nesting level over quantifier nodes |
| 155 | average applied-UF arity | Mean declared arity over distinct uninterpreted functions applied with at least one argument |
| 156 | average applications per applied UF | Mean application count over those distinct functions |

An empty collection produces `0.0`. If parsing or bounded AST traversal fails
in batch mode, IDs 146-156 are missing (`NaN`/empty CSV cell), while grammar
counts and source size are retained.

## Dataset identity and missing values

Dataset rows begin with:

```text
dataset_id, feature_schema, benchmark_sha256, relative_path, logic, family,
expected_status, extraction_status, error
```

`expected_status` is accepted only from one non-conflicting top-level
`(set-info :status sat|unsat|unknown)` command. Single-file AST-only extraction
uses `-1.0` plus `unavailableFeatureIds` for source inputs that were not
provided.

The batch extractor validates the dataset manifest and per-logic file counts,
writes table/schema/manifest through temporary siblings, and publishes the
completion manifest last. The manifest contains input, output and schema
hashes, code revision, dependency versions, and extraction counts.

## Contract evidence

Seven parsed golden fixtures cover Boolean, arrays, BV, FP, arithmetic,
quantifiers, and UF. Their expected values live in a TSV separate from the
extractor. Unit tests additionally cover every grammar position and structural
definition. This establishes the local `v1` contract, not historical MachSMT
model compatibility.
