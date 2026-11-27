#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow
mkdir -p deps evidence/access
fetch() {
    local tag="$1" url="$2" dest="$3"
    if [[ -s "$dest" ]]; then return; fi
    local stamp rc
    stamp="$(date +%s%N)"
    date -Is > "evidence/access/$tag-$stamp.start"
    rc=0
    curl --fail --location --retry 2 --max-time 180 "$url" -o "$dest.part" >"evidence/access/$tag-$stamp.log" 2>&1 || rc=$?
    printf '%s %s\n' "$(date -Is)" "$rc" >"evidence/access/$tag-$stamp.stop"
    if (( rc )); then return "$rc"; fi
    mv "$dest.part" "$dest"
}
fetch yosys 'https://deb.debian.org/debian/pool/main/y/yosys/yosys_0.23-6_amd64.deb' deps/yosys_0.23-6_amd64.deb
if [[ ! -x deps/yosys/usr/bin/yosys ]]; then
    mkdir -p deps/yosys
    bsdtar -xOf deps/yosys_0.23-6_amd64.deb data.tar.xz | tar -xJ -C deps/yosys
fi
fetch cvc5 'https://github.com/cvc5/cvc5/releases/download/cvc5-1.4.1/cvc5-Linux-x86_64-static.zip' deps/cvc5.zip
if [[ ! -x deps/cvc5-Linux-x86_64-static/bin/cvc5 ]]; then unzip -q -o deps/cvc5.zip -d deps; fi
fetch dafny 'https://github.com/dafny-lang/dafny/releases/download/v4.11.0/dafny-4.11.0-x64-ubuntu-22.04.zip' deps/dafny.zip
if [[ ! -x deps/dafny/dafny ]]; then unzip -q -o deps/dafny.zip -d deps; fi
mkdir -p deps/shims
ln -sfn ../dafny/z3/bin/z3-4.14.1 deps/shims/z3
sha256sum deps/yosys_0.23-6_amd64.deb deps/cvc5.zip deps/dafny.zip \
    deps/yosys/usr/bin/yosys deps/yosys/usr/bin/yosys-smtbmc deps/yosys/usr/share/yosys/smtio.py \
    deps/cvc5-Linux-x86_64-static/bin/cvc5 deps/dafny/dafny deps/dafny/z3/bin/z3-4.14.1 \
    "$(readlink -f "$(command -v codex)")" >evidence/deps.sha256.tmp
mv evidence/deps.sha256.tmp evidence/deps.sha256
{
    date -Is
    deps/yosys/usr/bin/yosys -V
    deps/cvc5-Linux-x86_64-static/bin/cvc5 --version
    deps/dafny/dafny --version
    deps/dafny/z3/bin/z3-4.14.1 --version
    codex --version
    ../campaign/.venv/bin/python -B -c 'import importlib.metadata as m; print("SMAC",m.version("smac"),"ConfigSpace",m.version("ConfigSpace"))'
} >evidence/versions.tmp
mv evidence/versions.tmp evidence/versions.txt
