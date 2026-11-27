#!/bin/bash
set -u
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH" TZ=Europe/Moscow UV_CACHE_DIR="$PWD/.uv-cache"
deadline=$(date -d '2026-11-24 11:13:40' +%s)
while (( $(date +%s) < deadline - 60 )); do
  start=$(date +%s)
  left=$((deadline-start-10))
  timeout -k 5s "${left}s" codex exec --ephemeral --skip-git-repo-check --sandbox workspace-write -c sandbox_workspace_write.network_access=true -m gpt-6.2-sol -o "summary-$start.txt" - <brief.txt >"engineer-$start.log" 2>&1
  rc=$?
  printf '%s %s\n' "$(date -Is)" "$rc" >> controller.log
  (( $(date +%s) >= deadline - 180 )) && break
  sleep 10
done
date -Is > finished
