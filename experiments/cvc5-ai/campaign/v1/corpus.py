"""Generate complete synthetic incremental QF_BV path sessions and their oracle."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random


FAMILIES = ("subset", "word", "product")
LEVELS = {
    "subset": (9, 11, 13, 15, 17, 19),
    "word": (7, 9, 11, 12, 13, 14),
    "product": (4, 5, 6, 7, 8, 9),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bv(number, width):
    return f"(_ bv{number % (1 << width)} {width})"


def declaration(name, width):
    return f"(declare-const {name} (_ BitVec {width}))"


def has_bit(name, index, value):
    return f"(= ((_ extract {index} {index}) {name}) #b{value})"


def define(name, expression, width):
    return f"(define-fun {name} () (_ BitVec {width}) {expression})"


def target_pair(rng, attainable, limit, probe=None):
    """Use a concrete witness and an exhaustive range membership test."""
    ordered = sorted(attainable)
    assert ordered
    witness = rng.choice(ordered[len(ordered) // 4: max(1, 3 * len(ordered) // 4)])
    origin = witness if probe is None else probe
    for distance in range(1, limit):
        for value in (origin + distance, origin - distance):
            if 0 <= value < limit and value not in attainable:
                return [(witness, "sat"), (value, "unsat")]
    raise ValueError("range has no missing value")


def subset(rng, choices):
    weights = [rng.randrange(1, 1 << (choices + 1)) for _ in range(choices)]
    signs = [rng.choice((-1, 1)) for _ in weights]
    width = (sum(weights) * 2).bit_length() + 1
    modulus = 1 << width
    nodes = [declaration("x", choices)]
    work = []
    gate_ops = []
    for index, weight in enumerate(weights):
        # Keep one independent bit per decision, while changing the expression graph.
        bit = rng.randrange(2)
        sign = signs[index]
        on, off = (weight, 0) if bit else (0, weight)
        operation = rng.choice(("ite", "mask", "multiply"))
        gate_ops.append(operation)
        if operation == "ite":
            expr = f"(ite {has_bit('x', index, bit)} {bv(sign * on, width)} {bv(sign * off, width)})"
        else:
            bit_expr = f"((_ extract {index} {index}) x)"
            factor = f"((_ zero_extend {width - 1}) {bit_expr})"
            if operation == "mask":
                expr = f"(bvand (bvneg {factor}) {bv(sign * weight, width)})"
            else:
                expr = f"(bvmul {factor} {bv(sign * weight, width)})"
        work.append((expr, str(index)))
    topology = []
    while len(work) > 1:
        left, left_shape = work.pop(rng.randrange(len(work)))
        right, right_shape = work.pop(rng.randrange(len(work)))
        topology.append(f"{left_shape}>{right_shape}")
        work.append((f"(bvadd {left} {right})", f"({left_shape},{right_shape})"))
    expression, _ = work[0]
    offset = sum(weight for weight, sign in zip(weights, signs) if sign < 0)
    nodes.append(define("out", f"(bvadd {bv(offset, width)} {expression})", width))
    selected = rng.randrange(choices)
    truth = rng.randrange(2)
    wanted = []
    for fixed in (False, True):
        values = {offset}
        for index, (weight, sign) in enumerate(zip(weights, signs)):
            if fixed and index == selected:
                values = {value + truth * weight * sign for value in values}
            else:
                values |= {value + weight * sign for value in values}
        wanted.append(target_pair(rng, values, modulus))
    shape = {"choice_count": choices, "gates": gate_ops, "addition": topology, "guard_index": selected}
    return nodes, has_bit("x", selected, truth), width, wanted, shape


def word(rng, width):
    modulus = 1 << width
    mask = modulus - 1
    n_stages = rng.randrange(4, 10)
    nodes = [declaration("x", width)]
    operations = []
    for i in range(n_stages):
        operator = rng.choice(("xor", "add", "mul", "asr", "rol", "branch", "mix", "or"))
        previous = i
        other = rng.randrange(i + 1)
        constant = rng.randrange(1, modulus)
        shift = rng.randrange(1, width)
        operations.append((operator, previous, other, constant, shift))
        a = "x" if previous == 0 else f"v{previous}"
        b = "x" if other == 0 else f"v{other}"
        k = bv(constant, width)
        lookup = {
            "xor": f"(bvxor {a} (bvadd {b} {k}))",
            "add": f"(bvadd {a} (bvxor {b} {k}))",
            "mul": f"(bvmul {a} (bvor {b} {k}))",
            "asr": f"(bvxor {a} (bvashr {b} {bv(shift, width)}))",
            "rol": f"(bvadd ((_ rotate_left {shift}) {a}) (bvxor {b} {k}))",
            "branch": f"(ite (bvslt {a} {k}) (bvsub {a} {b}) (bvxor {a} {k}))",
            "mix": f"(bvxor (bvmul {a} {k}) (bvlshr {b} {bv(shift, width)}))",
            "or": f"(bvadd {a} (bvor {b} {k}))",
        }
        name = "out" if i + 1 == n_stages else f"v{i + 1}"
        nodes.append(define(name, lookup[operator], width))

    def signed(x):
        return x if x < modulus // 2 else x - modulus

    def run(x):
        values = [x]
        for op, p, o, k, s in operations:
            a, b = values[p], values[o]
            if op == "xor":
                value = a ^ (b + k)
            elif op == "add":
                value = a + (b ^ k)
            elif op == "mul":
                value = a * (b | k)
            elif op == "asr":
                value = a ^ (signed(b) >> s)
            elif op == "rol":
                value = (((a << s) & mask) | (a >> (width - s))) + (b ^ k)
            elif op == "branch":
                value = a - b if signed(a) < signed(k) else a ^ k
            elif op == "mix":
                value = (a * k) ^ (b >> s)
            else:
                value = a + (b | k)
            values.append(value & mask)
        return values[-1]

    guard_bit = rng.randrange(width)
    truth = rng.randrange(2)
    outputs = [(x, run(x)) for x in range(modulus)]
    wanted = [
        target_pair(rng, {v for _, v in outputs}, modulus),
        target_pair(rng, {v for x, v in outputs if (x >> guard_bit) & 1 == truth}, modulus),
    ]
    shape = {"width": width, "operations": [(op, p, o, s if op in ("asr", "rol", "mix") else 0)
                                              for op, p, o, _, s in operations], "guard_bit": guard_bit}
    return nodes, has_bit("x", guard_bit, truth), width, wanted, shape


def product(rng, operand_width):
    modulus = 1 << operand_width
    width = 2 * operand_width
    nodes = [declaration("x", operand_width), declaration("y", operand_width)]
    operations = {}
    for name in ("x", "y"):
        previous = name
        chain = []
        for i in range(rng.randrange(2, 6)):
            op = rng.choice(("bvxor", "bvadd", "bvmul", "rol"))
            k = rng.randrange(1, modulus, 2)
            shift = rng.randrange(1, operand_width)
            expression = (f"((_ rotate_left {shift}) {previous})" if op == "rol"
                          else f"({op} {previous} {bv(k, operand_width)})")
            new_name = f"{name}{i}"
            nodes.append(define(new_name, expression, operand_width))
            previous = new_name
            chain.append((op, k, shift))
        operations[name] = (chain, previous)

    def run(name, x):
        for op, k, s in operations[name][0]:
            if op == "bvxor":
                x ^= k
            elif op == "bvadd":
                x += k
            elif op == "bvmul":
                x *= k
            else:
                x = (x << s) | (x >> (operand_width - s))
            x %= modulus
        return x

    left, right = (operations[name][1] for name in ("x", "y"))
    ext = f"(_ zero_extend {operand_width})"
    nodes.append(define("out", f"(bvmul ({ext} {left}) ({ext} {right}))", width))
    lower = rng.randrange(modulus // 4)
    upper = rng.randrange(3 * modulus // 4, modulus)
    nodes.append(f"(assert (bvult {left} {bv(upper, operand_width)}))")
    nodes.append(f"(assert (bvuge {right} {bv(lower, operand_width)}))")
    guard_bit = rng.randrange(operand_width)
    truth = rng.randrange(2)
    xs = [(x, run("x", x)) for x in range(modulus)]
    ys = [run("y", y) for y in range(modulus)]
    wanted = []
    for fixed in (False, True):
        attainable = {a * b for x, a in xs if a < upper and (not fixed or (x >> guard_bit) & 1 == truth)
                      for b in ys if b >= lower}
        wanted.append(target_pair(rng, attainable, 1 << width))
    shape = {"operand_width": operand_width, "guard_bit": guard_bit,
             **{name: [(op, shift if op == "rol" else 0) for op, _, shift in operations[name][0]]
                for name in ("x", "y")}}
    return nodes, has_bit("x", guard_bit, truth), width, wanted, shape


def shape_tokens(family, shape):
    """Normalize away literals, then retain operator topology and operand position."""
    if family == "subset":
        sequence = [f"gate:{i}:{op}" for i, op in enumerate(shape["gates"])]
        sequence += [f"edge:{x}" for x in shape["addition"]]
        sequence.append(f"guard:{shape['guard_index']}")
    elif family == "word":
        sequence = [f"{i}:{op}:{p - o}:{shift}" for i, (op, p, o, shift) in enumerate(shape["operations"])]
        sequence += [f"width:{shape['width']}", f"guard:{shape['guard_bit']}"]
    else:
        sequence = [f"{axis}:{i}:{op}:{shift}" for axis in ("x", "y")
                    for i, (op, shift) in enumerate(shape[axis])]
        sequence += [f"width:{shape['operand_width']}", f"guard:{shape['guard_bit']}"]
    return sorted(set(sequence + [f"{a}|{b}" for a, b in itertools.pairwise(sequence)]))


def sample(family, size, rng):
    nodes, guard, width, wanted, shape = globals()[family](rng, size)
    commands = ["(set-logic QF_BV)", *nodes]
    answers = []
    for branch, targets in enumerate(wanted):
        if branch:
            commands.extend(["(push 1)", f"(assert {guard})"])
        rng.shuffle(targets)
        for target, answer in targets:
            commands.extend(["(push 1)", f"(assert (= out {bv(target, width)}))", "(check-sat)", "(pop 1)"])
            answers.append(answer)
    commands.extend(["(pop 1)", "(exit)"])
    return "\n".join(commands) + "\n", answers, shape


def create(output, seed, split, sizes, count, exclusions=(), max_similarity=0.72):
    output.mkdir(parents=True, exist_ok=False)
    (output / "sessions").mkdir()
    rng = random.Random(seed)
    prior = [item for path in exclusions for item in json.loads(path.read_text())["sessions"]]
    used = {item["sha256"] for item in prior}
    accepted = {family: [set(i["shape_tokens"]) for i in prior if i["family"] == family]
                for family in FAMILIES}
    records = []
    for family in FAMILIES:
        for index in range(count):
            size = sizes[family][index % len(sizes[family])]
            for attempt in range(1000):
                try:
                    body, answers, shape = sample(family, size, rng)
                except ValueError:
                    continue
                digest = sha(body.encode())
                tokens = set(shape_tokens(family, shape))
                similarity = max((len(tokens & other) / len(tokens | other) for other in accepted[family]),
                                 default=0.0)
                if digest not in used and similarity < max_similarity:
                    break
            else:
                raise RuntimeError(f"could not generate distinct {family} size {size}")
            group = f"{split}-{family}-{index:03d}"
            relative = f"sessions/{group}.smt2"
            (output / relative).write_text(body)
            accepted[family].append(tokens)
            used.add(digest)
            records.append({"group": group, "file": relative, "family": family, "size": size,
                            "sha256": digest, "oracle": answers, "shape": shape,
                            "shape_tokens": sorted(tokens), "max_prior_similarity": similarity,
                            "generation_attempts": attempt + 1})
    manifest = {"format": 1, "scope": "synthetic straight-line QF_BV path sessions",
                "split": split, "seed": f"{seed:x}", "sizes": sizes, "group_count": len(records),
                "max_shape_jaccard_exclusive": max_similarity,
                "exclusions": [sha(path.read_bytes()) for path in exclusions], "sessions": records}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("split")
    parser.add_argument("--seed", required=True, type=lambda x: int(x, 16))
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--sizes", type=json.loads, default=LEVELS)
    parser.add_argument("--exclude", type=Path, action="append", default=[])
    args = parser.parse_args()
    create(args.output, args.seed, args.split, args.sizes, args.count, args.exclude)


if __name__ == "__main__":
    main()
