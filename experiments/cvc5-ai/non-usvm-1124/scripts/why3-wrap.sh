#!/usr/bin/env bash
# Relocate the unmodified Debian Why3 binary and drivers in a mount namespace.
set -euo pipefail
root="$(realpath "$(dirname "$0")/..")"
out="$(realpath "$1")"
shift
pkg="$root/deps/why3/usr"
if [[ ! -f "$out/why3.conf" ]]; then : > "$out/why3.conf"; fi
exec bwrap --unshare-net --unshare-pid --unshare-ipc --new-session --die-with-parent --clearenv \
    --ro-bind /usr/lib /hostlib --symlink hostlib /lib --symlink hostlib /lib64 \
    --dir /usr --ro-bind /usr/bin /usr/bin --symlink usr/bin /bin \
    --dir /usr/lib --ro-bind "$pkg/lib/x86_64-linux-gnu" /usr/lib/x86_64-linux-gnu \
    --dir /usr/share --ro-bind "$pkg/share/why3" /usr/share/why3 \
    --ro-bind "$root" /campaign --bind "$out" /work --dev /dev --proc /proc --tmpfs /tmp \
    --setenv HOME /work --setenv LD_LIBRARY_PATH /hostlib --setenv LANG C.UTF-8 \
    --setenv WHY3CONFIG /work/why3.conf --setenv PATH /campaign/deps/cvc5-Linux-x86_64-static/bin:/campaign/deps/shims:/usr/bin:/bin \
    --chdir /work /campaign/deps/why3/usr/bin/why3 "$@"
