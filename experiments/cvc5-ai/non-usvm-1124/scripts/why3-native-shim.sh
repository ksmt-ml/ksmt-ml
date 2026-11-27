#!/usr/bin/env bash
# Called by the native cvc5 driver to retain the original proof input/output.
set -euo pipefail
source_path="${@: -1}"
cp -- "$source_path" /work/native.smt2
printf '%q ' "$@" > /work/solver.argv
printf '\n' >> /work/solver.argv
/campaign/deps/cvc5-Linux-x86_64-static/bin/cvc5 --lang=smt2 "$@" | tee /work/solver.stdout
