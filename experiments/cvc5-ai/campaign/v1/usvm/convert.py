"""Convert ordered KSMT traces into whole sessions, preserving operation order."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


TOKEN = re.compile(r'\s+|;[^\n]*|\(|\)|"(?:""|[^"])*"|\|[^|]*\||[^\s();]+')
DECLARE = {"declare-fun", "declare-const", "declare-sort", "declare-datatypes", "define-fun", "define-sort"}


def terms(text):
    stack = []
    result = []
    end = 0
    for match in TOKEN.finditer(text):
        if match.start() != end:
            raise ValueError("cannot tokenize serializer output")
        end = match.end()
        token = match.group()
        if token.isspace() or token.startswith(";"):
            continue
        if token == "(":
            stack.append([])
        elif token == ")":
            item = stack.pop()
            if stack:
                stack[-1].append(item)
            else:
                result.append(item)
        elif stack:
            stack[-1].append(token)
        else:
            raise ValueError(f"unexpected top-level token {token}")
    if stack or end != len(text):
        raise ValueError("unbalanced serializer output")
    return result


def render(term):
    if isinstance(term, list):
        return "(" + " ".join(map(render, term)) + ")"
    return term


def convert(source):
    declarations = {}
    cmds = []
    oracle = []
    assumption = []
    tag = None
    depth = 0
    closed = False
    n_model = 0
    n_core = 0
    events = {}
    for line in source.read_text().splitlines():
        op, arg, encoded = line.split("\t")
        text = base64.b64decode(encoded).decode()
        events[op] = events.get(op, 0) + 1
        if closed:
            raise ValueError("operation after close")
        if op == "open":
            tag = text
        elif op in ("assert", "assumption"):
            assertions = []
            for term in terms(text):
                if term[0] in DECLARE:
                    # Preserve term-declaration dependency order. KSMT declarations
                    # live in its expression context, outside the solver scopes.
                    symbol = render(term[1])
                    declaration = render(term)
                    if symbol in declarations and declarations[symbol] != declaration:
                        raise ValueError(f"incompatible declarations for {symbol}")
                    declarations[symbol] = declaration
                elif term[0] == "assert" and len(term) == 2:
                    assertions.append(render(term[1]))
                else:
                    raise ValueError(f"unexpected native command {term[0]}")
            if not assertions:
                raise ValueError("missing serialized expression")
            if op == "assumption":
                assumption.append(assertions[0] if len(assertions) == 1 else
                                  "(and " + " ".join(assertions) + ")")
            else:
                cmds.extend(f"(assert {term})" for term in assertions)
        elif op.startswith("check"):
            status = arg.lower()
            if status not in ("sat", "unsat"):
                raise ValueError(f"oracle {status}")
            oracle.append(status)
            if op == "checkWithAssumptions":
                cmds.append("(check-sat-assuming (" + " ".join(assumption) + "))")
                assumption.clear()
            elif op == "check" and not assumption:
                cmds.append("(check-sat)")
            else:
                raise ValueError(f"unexpected {op} with buffered assumptions")
        elif op == "push":
            depth += 1
            cmds.append("(push 1)")
        elif op == "pop":
            depth -= int(arg)
            if depth < 0:
                raise ValueError("pop underflow")
            cmds.append(f"(pop {arg})")
        elif op == "model":
            if not oracle or oracle[-1] != "sat":
                raise ValueError("model without SAT response")
            n_model += 1
            cmds.append("(get-model)")
        elif op == "unsatCore":
            if not oracle or oracle[-1] != "unsat" or not cmds[-1].startswith("(check-sat-assuming"):
                raise ValueError("unsupported unsat core request")
            n_core += 1
            cmds.append("(get-unsat-assumptions)")
        elif op == "coreTerm":
            # The logged Z3 core terms record its selected subset. Other correct
            # solvers may return another sufficient subset.
            pass
        elif op == "close":
            closed = True
        else:
            raise ValueError(f"untranslated solver operation {op}")
    if not tag or depth != 0 or not oracle or assumption:
        raise ValueError("incomplete solver transcript")
    header = ["(set-logic ALL)"]
    if n_model:
        header.append("(set-option :produce-models true)")
    if n_core:
        header.append("(set-option :produce-unsat-assumptions true)")
    session = "\n".join([*header, *declarations.values(), *cmds, "(exit)"]) + "\n"
    return session, tag, oracle, events


def create(base):
    raw = base / "traces"
    traces = sorted(raw.glob("*.events"))
    metadata = json.loads((base / "programs.json").read_text())
    methods = {x["tag"]: x for x in map(json.loads, (raw / "methods.jsonl").read_text().splitlines())}
    programs = {p["class"]: p for p in metadata["programs"]}
    (base / "sessions").mkdir(exist_ok=True)
    accepted = []
    rejected = []
    for trace in traces:
        try:
            session, tag, oracle, operations = convert(trace)
            cls = tag.split("/", 1)[1].split(".run", 1)[0]
            program = programs[cls]
            if methods[tag]["status"] != "ok":
                raise ValueError(f"method ended with {methods[tag]['status']}")
        except (ValueError, KeyError, IndexError) as exc:
            rejected.append({"file": trace.name, "reason": str(exc), "sha256": hash_bytes(trace.read_bytes())})
            continue
        file = f"sessions/{program['group']}-{trace.stem}.smt2"
        (base / file).write_text(session)
        accepted.append({"group": program["group"], "family": "java", "size": program["width"],
                         "shape_tokens": program["shape_tokens"], "id": trace.stem, "tag": tag,
                         "file": file, "sha256": hash_bytes(session.encode()), "oracle": oracle,
                         "trace": str(trace.relative_to(base)), "trace_sha256": hash_bytes(trace.read_bytes()),
                         "source_sha256": program["sha256"], "operations": operations})
    manifest = {"format": 1, "scope": "instrumented USVM JVM KSMT solver sessions",
                "split": metadata["split"], "seed": metadata["seed"],
                "group_count": len({item["group"] for item in accepted}), "sessions": accepted,
                "pinned_usvm": "814a963354209421d36af758edbcec4295b081c4", "ksmt_version": "0.5.26",
                "programs_sha256": hash_bytes((base / "programs.json").read_bytes()), "rejected": rejected}
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"{len(accepted)} sessions / {manifest['group_count']} programs accepted, {len(rejected)} rejected")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("directory", type=Path)
    create(p.parse_args().directory)
