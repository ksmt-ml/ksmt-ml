#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/setup/uv-python"
mkdir -p setup
export TMPDIR="$PWD/setup"
if [ ! -x .venv/bin/python ]; then
  uv python install --no-bin 3.12.13
  uv venv --python 3.12.13 .venv
fi
uv pip sync --python .venv/bin/python setup/requirements-z3.lock
if [ ! -x setup/cvc5/cvc5-Linux-x86_64-static/bin/cvc5 ]; then
  curl -fsSL --max-time 150 \
    https://github.com/cvc5/cvc5/releases/download/cvc5-1.4.1/cvc5-Linux-x86_64-static.zip \
    -o setup/cvc5.zip
  printf '%s  %s\n' 2f8efe58fe27ba7bccbb504533f690b9312d69da14192712460e4a19231f02a1 setup/cvc5.zip | sha256sum -c -
  unzip -q setup/cvc5.zip -d setup/cvc5
fi
