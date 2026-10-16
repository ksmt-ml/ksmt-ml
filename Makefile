.PHONY: check analysis-env search-env onnx-env

check:
	bash scripts/hygiene.sh
	python3 -m compileall -q src tests
	PYTHONPATH=src python3 -m unittest discover -s tests
	@for schema in schemas/*.json; do python3 -m json.tool "$$schema" >/dev/null; done

analysis-env:
	python3 -m venv .venv
	./.venv/bin/pip install -r requirements-analysis.txt

search-env:
	python3 -m venv .venv
	./.venv/bin/pip install -r requirements-search.txt

onnx-env:
	python3 -m venv .venv
	./.venv/bin/pip install -r requirements-onnx.txt
