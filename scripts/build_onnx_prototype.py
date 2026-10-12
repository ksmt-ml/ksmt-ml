import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, checker, helper, numpy_helper


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DIR = ROOT / "model-bundles" / "cvc5-policy-prototype-v1"
MODEL_PATH = BUNDLE_DIR / "model.onnx"
MANIFEST_PATH = BUNDLE_DIR / "manifest.json"
TEST_VECTORS_PATH = BUNDLE_DIR / "test-vectors.json"
CHECKSUMS_PATH = BUNDLE_DIR / "sha256sums.txt"

BUNDLE_FORMAT = "ksmt-ml-onnx-bundle-v1"
BUNDLE_ID = "cvc5-policy-prototype-v1"
FEATURE_SCHEMA = "ksmt-machsmt-inspired-v1"
FEATURE_COUNT = 157
ACTION_SPACE = "cvc5-policy-prototype-actions-v1"

INPUT_NAME = "features"
OUTPUT_NAMES = ["action_scores", "action_id", "config_values", "config_mask"]


def bool_parameter(index: int, name: str, group: str, default: bool) -> dict:
    return {
        "index": index,
        "name": name,
        "group": group,
        "type": "bool",
        "default": str(default).lower(),
        "encoding": {"false": 0, "true": 1},
    }


def enum_parameter(
    index: int, name: str, group: str, default: str, values: tuple[str, ...]
) -> dict:
    if default not in values:
        raise ValueError(f"default {default!r} is not an encoding for {name}")
    ordered_values = (default,) + tuple(value for value in values if value != default)
    return {
        "index": index,
        "name": name,
        "group": group,
        "type": "enum",
        "default": default,
        "encoding": {value: code for code, value in enumerate(ordered_values)},
    }


# This is the complete v1 output vocabulary. The values and defaults come from
# cvc5 1.3.4 getOptionInfo(), captured in cvc5-options.txt. Keeping the catalog
# finite avoids asking the first learned model to synthesize unchecked strings.
PARAMETERS = [
    enum_parameter(0, "decision", "general", "internal", ("internal", "justification", "stoponly")),
    enum_parameter(1, "simplification", "general", "batch", ("batch", "none")),
    enum_parameter(2, "sat-solver", "general", "minisat", ("minisat", "cadical")),
    enum_parameter(
        3,
        "deep-restart",
        "general",
        "none",
        ("none", "all", "input", "input-and-prop", "input-and-solvable"),
    ),
    enum_parameter(4, "lemma-inprocess", "general", "none", ("none", "light", "full")),
    enum_parameter(5, "preregister-mode", "general", "eager", ("eager", "lazy")),
    enum_parameter(6, "arith-prop", "arithmetic", "both", ("none", "unate", "bi", "both")),
    bool_parameter(7, "arith-rewrite-equalities", "arithmetic", False),
    bool_parameter(8, "arith-static-learning", "arithmetic", True),
    bool_parameter(9, "miplib-trick", "arithmetic", False),
    enum_parameter(10, "unate-lemmas", "arithmetic", "all", ("all", "eqs", "ineqs", "none")),
    enum_parameter(11, "nl-ext", "arithmetic", "full", ("full", "light", "none")),
    bool_parameter(12, "nl-cov", "arithmetic", True),
    bool_parameter(13, "arrays-eager-index", "arrays", True),
    bool_parameter(14, "arrays-eager-lemmas", "arrays", False),
    bool_parameter(15, "arrays-optimize-linear", "arrays", True),
    bool_parameter(16, "arrays-reduce-sharing", "arrays", False),
    bool_parameter(17, "arrays-weak-equiv", "arrays", False),
    enum_parameter(18, "bitblast", "bitvectors", "lazy", ("lazy", "eager")),
    enum_parameter(
        19,
        "bv-sat-solver",
        "bitvectors",
        "cadical",
        ("cadical", "cryptominisat", "kissat"),
    ),
    bool_parameter(20, "bv-eager-eval", "bitvectors", False),
    bool_parameter(21, "bv-gauss-elim", "bitvectors", False),
    bool_parameter(22, "bv-propagate", "bitvectors", True),
    enum_parameter(
        23,
        "solve-bv-as-int",
        "bitvectors",
        "off",
        ("off", "bitwise", "bv", "iand", "sum"),
    ),
    bool_parameter(24, "cbqi", "quantifiers", True),
    enum_parameter(25, "cbqi-mode", "quantifiers", "prop-eq", ("prop-eq", "conflict")),
    bool_parameter(26, "e-matching", "quantifiers", True),
    bool_parameter(27, "finite-model-find", "quantifiers", False),
    bool_parameter(28, "mbqi", "quantifiers", False),
    enum_parameter(
        29,
        "miniscope-quant",
        "quantifiers",
        "conj-and-fv",
        ("conj-and-fv", "agg", "conj", "fv", "off"),
    ),
]

PARAMETER_BY_NAME = {parameter["name"]: parameter for parameter in PARAMETERS}

ACTIONS = [
    {"id": 0, "name": "default", "options": {}},
    {
        "id": 1,
        "name": "arrays-eager-lemmas",
        "options": {"arrays-eager-lemmas": "true"},
    },
    {
        "id": 2,
        "name": "arith-prop-unate",
        "options": {"arith-prop": "unate"},
    },
    {
        "id": 3,
        "name": "decision-justification",
        "options": {"decision": "justification"},
    },
]


def encode_action(options: dict[str, str]) -> tuple[list[int], list[bool]]:
    values = [0] * len(PARAMETERS)
    mask = [False] * len(PARAMETERS)
    for name, value in options.items():
        parameter = PARAMETER_BY_NAME[name]
        values[parameter["index"]] = parameter["encoding"][value]
        mask[parameter["index"]] = True
    return values, mask


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_model() -> None:
    # The graph is intentionally a transparent, hand-authored heuristic. It is
    # an executable interface fixture, not a trained or evaluated selector.
    weights = np.zeros((FEATURE_COUNT, 4), dtype=np.float32)

    # Action 1: prefer eager array lemmas when array operations are present.
    for feature_id in (46, 47, 48):
        weights[feature_id - 1, 1] = 0.75

    # Action 2: prefer unate arithmetic propagation for arithmetic-heavy input.
    for feature_id in range(131, 146):
        weights[feature_id - 1, 2] = 0.35

    # Action 3: prefer justification decisions for quantified input.
    for feature_id in (32, 33, 151, 152, 154):
        weights[feature_id - 1, 3] = 0.75

    bias = np.asarray([0.25, 0.0, 0.0, 0.0], dtype=np.float32)
    one = np.asarray([1.0], dtype=np.float32)

    encoded_actions = [encode_action(action["options"]) for action in ACTIONS]
    config_table = np.asarray([item[0] for item in encoded_actions], dtype=np.int64)
    config_mask_table = np.asarray(
        [item[1] for item in encoded_actions], dtype=np.bool_
    )

    nodes = [
        helper.make_node("Add", [INPUT_NAME, "one"], ["shifted_features"]),
        helper.make_node("Log", ["shifted_features"], ["log_features"]),
        helper.make_node("MatMul", ["log_features", "weights"], ["linear_scores"]),
        helper.make_node("Add", ["linear_scores", "bias"], ["action_scores"]),
        helper.make_node(
            "ArgMax",
            ["action_scores"],
            ["action_id"],
            axis=1,
            keepdims=0,
            select_last_index=0,
        ),
        helper.make_node(
            "Gather", ["config_table", "action_id"], ["config_values"], axis=0
        ),
        helper.make_node(
            "Gather", ["config_mask_table", "action_id"], ["config_mask"], axis=0
        ),
    ]

    graph = helper.make_graph(
        nodes=nodes,
        name=BUNDLE_ID,
        inputs=[
            helper.make_tensor_value_info(
                INPUT_NAME, TensorProto.FLOAT, ["batch", FEATURE_COUNT]
            )
        ],
        outputs=[
            helper.make_tensor_value_info(
                "action_scores", TensorProto.FLOAT, ["batch", 4]
            ),
            helper.make_tensor_value_info("action_id", TensorProto.INT64, ["batch"]),
            helper.make_tensor_value_info(
                "config_values", TensorProto.INT64, ["batch", len(PARAMETERS)]
            ),
            helper.make_tensor_value_info(
                "config_mask", TensorProto.BOOL, ["batch", len(PARAMETERS)]
            ),
        ],
        initializer=[
            numpy_helper.from_array(one, "one"),
            numpy_helper.from_array(weights, "weights"),
            numpy_helper.from_array(bias, "bias"),
            numpy_helper.from_array(config_table, "config_table"),
            numpy_helper.from_array(config_mask_table, "config_mask_table"),
        ],
    )

    model = helper.make_model(
        graph,
        producer_name="ksmt-ml",
        producer_version="prototype-v1",
        opset_imports=[helper.make_operatorsetid("", 17)],
    )
    # IR v8 plus opset 17 keeps the fixture compatible with a broad range of
    # ONNX Runtime versions while still supporting every operator used here.
    model.ir_version = 8
    helper.set_model_props(
        model,
        {
            "ksmt_ml.bundle_format": BUNDLE_FORMAT,
            "ksmt_ml.bundle_id": BUNDLE_ID,
            "ksmt_ml.feature_schema": FEATURE_SCHEMA,
            "ksmt_ml.action_space": ACTION_SPACE,
            "ksmt_ml.status": "interface-prototype",
        },
    )
    checker.check_model(model, full_check=True)
    MODEL_PATH.write_bytes(model.SerializeToString(deterministic=True))


def build_manifest() -> None:
    manifest = {
        "bundleFormat": BUNDLE_FORMAT,
        "bundleId": BUNDLE_ID,
        "status": "interface-prototype",
        "purpose": (
            "Freeze and test the batched ONNX/JVM interface before a learned "
            "solver-selection model exists."
        ),
        "model": {
            "file": MODEL_PATH.name,
            "sha256": sha256(MODEL_PATH),
            "onnxIrVersion": 8,
            "opset": 17,
            "producer": "ksmt-ml",
            "kind": "hand-authored-linear-heuristic",
        },
        "input": {
            "name": INPUT_NAME,
            "dtype": "float32",
            "shape": ["batch", FEATURE_COUNT],
            "featureSchema": FEATURE_SCHEMA,
            "featureIdBase": 1,
            "valueContract": "finite and non-negative raw v1 feature values",
            "invalidInputPolicy": "reject; caller must use an explicit fallback",
        },
        "outputs": [
            {
                "name": "action_scores",
                "dtype": "float32",
                "shape": ["batch", 4],
                "semantics": "diagnostic preference scores; greater is preferred",
            },
            {
                "name": "action_id",
                "dtype": "int64",
                "shape": ["batch"],
                "semantics": "argmax(action_scores); ties select the lowest id",
            },
            {
                "name": "config_values",
                "dtype": "int64",
                "shape": ["batch", len(PARAMETERS)],
                "semantics": "encoded parameter values in parameters order",
            },
            {
                "name": "config_mask",
                "dtype": "bool",
                "shape": ["batch", len(PARAMETERS)],
                "semantics": "true only when the corresponding option must be set",
            },
        ],
        "parameters": PARAMETERS,
        "actionSpace": {
            "id": ACTION_SPACE,
            "solver": "cvc5",
            "solverVersion": "1.3.4",
            "validatedForPerformance": False,
            "actions": ACTIONS,
        },
        "selection": {
            "operation": "argmax",
            "scoreOutput": "action_scores",
            "selectedActionOutput": "action_id",
        },
        "limitations": [
            "The graph is not trained and has no performance claim.",
            "The prototype action space is an interface fixture, not E0005 evidence.",
            "The 30-parameter vocabulary is fixed, but the prototype actions set only one option at a time.",
            "The caller must verify featureSchema before inference.",
            "NaN, infinity, negative values, and partial feature rows are rejected.",
        ],
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def make_row(values_by_feature_id: dict[int, float]) -> list[float]:
    row = [0.0] * FEATURE_COUNT
    for feature_id, value in values_by_feature_id.items():
        if not 1 <= feature_id <= FEATURE_COUNT:
            raise ValueError(f"feature id out of range: {feature_id}")
        row[feature_id - 1] = value
    return row


def build_test_vectors() -> None:
    encoded_actions = [encode_action(action["options"]) for action in ACTIONS]
    test_vectors = {
        "bundleId": BUNDLE_ID,
        "featureSchema": FEATURE_SCHEMA,
        "features": [
            make_row({}),
            make_row({47: 3.0}),
            make_row({133: 10.0}),
            make_row({33: 2.0}),
        ],
        "expected": {
            "action_id": [0, 1, 2, 3],
            "config_values": [item[0] for item in encoded_actions],
            "config_mask": [item[1] for item in encoded_actions],
        },
    }
    TEST_VECTORS_PATH.write_text(
        json.dumps(test_vectors, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_checksums() -> None:
    paths = [MODEL_PATH, MANIFEST_PATH, TEST_VECTORS_PATH]
    CHECKSUMS_PATH.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in paths), encoding="utf-8"
    )


def main() -> None:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    build_model()
    build_manifest()
    build_test_vectors()
    write_checksums()
    print(BUNDLE_DIR)


if __name__ == "__main__":
    main()
