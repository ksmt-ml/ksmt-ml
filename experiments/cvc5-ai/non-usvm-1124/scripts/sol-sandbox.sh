#!/usr/bin/env bash
# The client uses its existing ChatGPT session. Only the staged train packet is
# visible to its command sandbox. Credential material stays with the client.
set -euo pipefail
call_dir="$(realpath "$1")"
train_dir="$(realpath "$2")"
protocol_dir="$(realpath "$3")"
codex_bin="$(realpath "$(command -v codex)")"
client_auth="$HOME/.codex/auth.json"
mkdir -p "$call_dir"/{work,tmp}
exec bwrap --unshare-pid --unshare-ipc --new-session --die-with-parent --clearenv   --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib /lib64 --symlink usr/bin /bin   --dir /etc --ro-bind /etc/resolv.conf /etc/resolv.conf   --ro-bind /etc/ssl /etc/ssl --ro-bind /etc/ca-certificates /etc/ca-certificates   --ro-bind-try /etc/hosts /etc/hosts --ro-bind-try /etc/nsswitch.conf /etc/nsswitch.conf   --proc /proc --dev /dev --bind "$call_dir/work" /work --bind "$call_dir/tmp" /tmp   --ro-bind "$train_dir" /train --ro-bind "$protocol_dir" /protocol   --ro-bind "$(dirname "$codex_bin")" /program --dir /runner --tmpfs /runner/state   --ro-bind "$client_auth" /runner/state/auth.json   --setenv HOME /work --setenv CODEX_HOME /runner/state --setenv PATH /usr/bin:/bin   --setenv TMPDIR /tmp --setenv LANG C.UTF-8 --chdir /work   /program/codex -a never exec --ephemeral --json --ignore-user-config --ignore-rules --skip-git-repo-check   -c 'default_permissions="experiment"'   -c 'permissions.experiment.filesystem={":minimal"="read","/work"="write","/train"="read","/protocol"="read","/tmp"="write","/program"="read","/runner"="deny","/proc"="deny"}'   -c 'permissions.experiment.network.enabled=false'   -c 'features.apps=false' -c 'features.memories=false' -c 'features.multi_agent=false' -c 'web_search="disabled"'   -c 'model_reasoning_effort="low"' -m gpt-6.2-sol   --output-schema /protocol/schema.json --output-last-message /work/answer.json -
