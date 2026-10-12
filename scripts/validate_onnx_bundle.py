import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
import onnx
from jsonschema import Draft202012Validator

os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
import onnxruntime as ort


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DIR = ROOT / "model-bundles" / "cvc5-policy-prototype-v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_cvc5_parameter_vocabulary(parameters: list[dict]) -> None:
    option_lines = {}
    for line in (ROOT / "cvc5-options.txt").read_text(encoding="utf-8").splitlines():
        if not line.startswith("OptionInfo{"):
            continue
        names = line.removeprefix("OptionInfo{").split("|", maxsplit=1)[0]
        for name in (item.strip() for item in names.split(",")):
            option_lines.setdefault(name, line)

    for parameter in parameters:
        name = parameter["name"]
        line = option_lines.get(name)
        assert line is not None, f"cvc5 1.3.4 does not expose option {name!r}"
        default_match = re.search(r"\bdefault ([^ |}]+)", line)
        assert default_match is not None
        assert default_match.group(1) == parameter["default"]

        if parameter["type"] == "bool":
            assert "| bool |" in line
            assert set(parameter["encoding"]) == {"false", "true"}
        elif parameter["type"] == "enum":
            assert "| mode |" in line
            modes_match = re.search(r"\bmodes: ([^}]+)", line)
            assert modes_match is not None
            modes = {item.strip() for item in modes_match.group(1).split(",")}
            assert set(parameter["encoding"]) == modes
        else:
            raise AssertionError(f"unsupported prototype parameter type: {parameter['type']}")


def main() -> None:
    manifest = json.loads((BUNDLE_DIR / "manifest.json").read_text(encoding="utf-8"))
    vectors = json.loads((BUNDLE_DIR / "test-vectors.json").read_text(encoding="utf-8"))
    manifest_schema = json.loads(
        (ROOT / "schemas" / "onnx-bundle-v1.schema.json").read_text(encoding="utf-8")
    )
    model_path = BUNDLE_DIR / manifest["model"]["file"]

    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator(manifest_schema).validate(manifest)
    validate_cvc5_parameter_vocabulary(manifest["parameters"])
    assert manifest["bundleFormat"] == "ksmt-ml-onnx-bundle-v1"
    assert manifest["status"] == "interface-prototype"
    assert manifest["input"]["featureSchema"] == vectors["featureSchema"]
    assert sha256(model_path) == manifest["model"]["sha256"]

    for line in (BUNDLE_DIR / "sha256sums.txt").read_text().splitlines():
        expected, filename = line.split("  ", maxsplit=1)
        assert sha256(BUNDLE_DIR / filename) == expected

    model = onnx.load(model_path)
    onnx.checker.check_model(model, full_check=True)

    session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    assert [item.name for item in session.get_inputs()] == ["features"]
    assert [item.name for item in session.get_outputs()] == [
        "action_scores",
        "action_id",
        "config_values",
        "config_mask",
    ]

    features = np.asarray(vectors["features"], dtype=np.float32)
    scores, action_id, config_values, config_mask = session.run(
        None, {"features": features}
    )

    parameter_count = len(manifest["parameters"])
    action_count = len(manifest["actionSpace"]["actions"])
    assert scores.shape == (features.shape[0], action_count)
    assert config_values.shape == (features.shape[0], parameter_count)
    assert config_mask.shape == (features.shape[0], parameter_count)
    np.testing.assert_array_equal(action_id, vectors["expected"]["action_id"])
    np.testing.assert_array_equal(config_values, vectors["expected"]["config_values"])
    np.testing.assert_array_equal(config_mask, vectors["expected"]["config_mask"])

    parameters = sorted(manifest["parameters"], key=lambda item: item["index"])
    assert [item["index"] for item in parameters] == list(range(parameter_count))
    actions = {item["id"]: item for item in manifest["actionSpace"]["actions"]}
    for row, selected_action in enumerate(action_id.tolist()):
        decoded = {}
        for parameter in parameters:
            index = parameter["index"]
            if config_mask[row, index]:
                values_by_code = {
                    code: value for value, code in parameter["encoding"].items()
                }
                decoded[parameter["name"]] = values_by_code[
                    int(config_values[row, index])
                ]
        assert decoded == actions[selected_action]["options"]

    print(
        f"validated {manifest['bundleId']}: batch={features.shape[0]}, "
        f"features={features.shape[1]}, actions={scores.shape[1]}, "
        f"parameters={parameter_count}"
    )


if __name__ == "__main__":
    main()
