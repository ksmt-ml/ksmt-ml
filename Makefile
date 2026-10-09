.PHONY: test check analysis-env notebook smoke

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

smoke:
	./gradlew run --args="--smoke-experiments"
