#!/usr/bin/env bash
# Write every HERDR_* environment variable (including the context/event JSON) to one file per invocation.
set -u
kind="${1:-unknown}"
dir="${HERDR_PLUGIN_STATE_DIR:-/tmp/mindmap-spike}"
mkdir -p "$dir"
stamp="$(date +%s%N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1e9))')"
out="$dir/${stamp}-${kind}.env"
{
  echo "KIND=$kind"
  echo "CWD=$PWD"
  env | grep '^HERDR_' | sort
} > "$out"
