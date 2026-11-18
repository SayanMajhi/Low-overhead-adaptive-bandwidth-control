#!/usr/bin/env bash
set -euo pipefail

export CN_ORIGINAL_NETNS=$(stat -Lc %i /proc/self/ns/net)
exec unshare -Urn python3 "$(dirname "$0")/netns.py" "$@"
