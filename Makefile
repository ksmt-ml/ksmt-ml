.PHONY: check analysis-env onnx-env

check:
	bash scripts/hygiene.sh
	python3 -m compileall -q src tests
	PYTHONPATH=src python3 -m unittest discover -s tests
	python3 -m json.tool schemas/cvc5-parameter-space-v1.schema.json >/dev/null
	python3 -m json.tool schemas/cvc5-tuner-bundle-v1.schema.json >/dev/null

analysis-env:
	python3 -m venv .venv
	./.venv/bin/pip install -r requirements-analysis.txt

onnx-env:
	python3 -m venv .venv
	./.venv/bin/pip install -r requirements-onnx.txt
