"""Make private, independent Java source groups for the pinned USVM driver."""
import argparse
import hashlib
import json
from pathlib import Path
import random


def make_group(rng, index, width):
    cls = f"Program{index:03d}"
    operations = []
    lines = [f"package pilot; public class {cls} {{",
             "  public static int run(int input, int other) {",
             f"    int x = input & {(1 << width) - 1};",
             f"    int y = other & {(1 << width) - 1};",
             "    int s = 0;"]
    for i in range(rng.randrange(3, 7)):
        op = rng.choice(("mix", "product", "signed", "shift", "mask", "sum", "remainder"))
        k = rng.randrange(3, 1 << min(width, 14), 2)
        b = rng.randrange(1, 10)
        c = rng.randrange(1, 1 << min(width, 14))
        a = rng.randrange(1, 1 << width)
        variant = rng.randrange(3)
        a_expr = ["x", "y", "s"][variant]
        b_expr = ["y", "s", "x"][variant]
        p = f"p{i}"
        expressions = {
            "mix": (f"(({a_expr} ^ {b_expr}) * {k}) ^ ({a_expr} >>> {b})", f"({p} & {a}) == {c & a}"),
            "product": (f"({a_expr} + {k}) * ({b_expr} | {b})", f"{p} == {c * k}"),
            "signed": (f"({a_expr} - {b_expr}) << {b}", f"({p} ^ {k}) < {c << b}"),
            "shift": (f"({a_expr} << ({b_expr} & 15)) ^ {k}", f"({p} >>> {b}) == {c}"),
            "mask": (f"({a_expr} | {a}) & ({b_expr} ^ {k})", f"({p} & {c}) != {a & c}"),
            "sum": (f"({a_expr} + {k}) ^ ({b_expr} - {c})", f"({p} & {a}) == {k & a}"),
            "remainder": (f"({a_expr} ^ {b_expr}) % {k}", f"{p} == {c % k}"),
        }
        expression, condition = expressions[op]
        updates = (f"s += {p};", f"x ^= {p};", f"y += {p};")
        alternate = (f"y ^= {p};", f"s -= {p};", f"x += {p};")
        lines += [f"    int {p} = {expression};",
                  f"    if ({condition}) {{ {updates[variant]} }} else {{ {alternate[variant]} }}"]
        operations.append(f"{i}:{op}:{variant}")
    lines += ["    return s ^ (x - y);", "  }", "}"]
    return cls, "\n".join(lines) + "\n", operations


def create(directory, seed, split, widths, count, exclude):
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "source/pilot"
    source.mkdir(parents=True)
    rng = random.Random(seed)
    records = []
    prior = [set(item["shape_tokens"]) for manifest in exclude
             for item in json.loads(manifest.read_text())["programs"]]
    for index in range(count):
        width = widths[index % len(widths)]
        for _ in range(1000):
            cls, data, shape = make_group(rng, index, width)
            tokens = set(shape)
            similarity = max((len(tokens & other) / len(tokens | other) for other in prior), default=0.0)
            if similarity < 0.72:
                break
        else:
            raise RuntimeError("could not generate distinct Java group")
        prior.append(tokens)
        dst = source / f"{cls}.java"
        dst.write_text(data)
        records.append({"class": f"pilot.{cls}", "group": f"{split}-{cls}", "width": width,
                        "source": str(dst.relative_to(directory)), "shape_tokens": shape,
                        "max_prior_similarity": similarity,
                        "sha256": hashlib.sha256(data.encode()).hexdigest()})
    (directory / "programs.json").write_text(json.dumps({
        "seed": f"{seed:x}", "split": split, "programs": records,
        "exclusions": [hashlib.sha256(path.read_bytes()).hexdigest() for path in exclude],
    }, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("directory", type=Path)
    p.add_argument("split")
    p.add_argument("--seed", type=lambda x: int(x, 16), required=True)
    p.add_argument("--widths", type=lambda s: [int(x) for x in s.split(",")], default=[5, 7, 9, 12])
    p.add_argument("--count", type=int, required=True)
    p.add_argument("--exclude", action="append", type=Path, default=[])
    a = p.parse_args()
    create(a.directory, a.seed, a.split, a.widths, a.count, a.exclude)
