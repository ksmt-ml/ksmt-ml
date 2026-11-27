#!/usr/bin/env bash
set -euo pipefail
call_dir="$(realpath "$1")"
train_dir="$(realpath "$2")"
protocol_dir="$(realpath "$3")"
exec /usr/bin/prlimit --as=1610612736 --core=0 --fsize=33554432 -- \
  bwrap --unshare-pid --unshare-ipc --unshare-net --new-session --die-with-parent --clearenv \
  --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib /lib64 --symlink usr/bin /bin \
  --proc /proc --dev /dev --bind "$call_dir/work" /work --bind "$call_dir/tmp" /tmp \
  --ro-bind "$train_dir" /train --ro-bind "$protocol_dir" /protocol \
  --setenv HOME /work --setenv PATH /usr/bin:/bin --setenv TMPDIR /tmp --setenv LANG C.UTF-8 \
  --setenv PYTHONDONTWRITEBYTECODE 1 --setenv SEARCH_SEED "$4" --chdir /work \
  /usr/bin/python3 -u /work/policy.py
