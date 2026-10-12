.PHONY: test check analysis-env notebook cvc5-options onnx-env onnx-prototype onnx-validate smoke

test:
	./gradlew test

check:
	bash scripts/hygiene.sh
	./gradlew clean test

analysis-env:
	python3 -m venv .venv
	./.venv/bin/pip install -r requirements-analysis.txt

notebook:
	./.venv/bin/jupyter nbconvert \
		--to notebook \
		--execute \
		--inplace \
		notebooks/01_smtlib_feature_distributions.ipynb

cvc5-options:
	./gradlew -q run --args="--list-options" > cvc5-options.txt

onnx-env:
	python3 -m venv .venv
	./.venv/bin/pip install -r requirements-onnx.txt

onnx-prototype:
	./.venv/bin/python scripts/build_onnx_prototype.py
	./.venv/bin/python scripts/validate_onnx_bundle.py

onnx-validate:
	./.venv/bin/python scripts/validate_onnx_bundle.py

smoke:
	./gradlew run --args="--smoke-experiments"
