#!/bin/bash
set -eu
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR="$PWD/.uv-cache"
exec timeout 12h codex exec --ephemeral --skip-git-repo-check --sandbox workspace-write -c sandbox_workspace_write.network_access=true -m gpt-6.2-sol -o summary.txt - <brief.txt > engineer.log 2>&1
