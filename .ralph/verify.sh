#!/usr/bin/env bash
# Ralph verify gate. Runs the unittest targets from the parent directory
# (the package import path is `yt_channel_analyzer.X`, so CWD must contain
# the package). Used by both the inner agent (PROMPT.md step 6) and the
# wrapper (afk-ralph.sh pre-flight + per-iteration check).
#
# Override the targets via env var or positional args:
#   RALPH_VERIFY_TARGETS="yt_channel_analyzer.test_discovery" .ralph/verify.sh
#   .ralph/verify.sh yt_channel_analyzer.test_discovery
set -euo pipefail

cd "$(git rev-parse --show-toplevel)/.."

DEFAULT_TARGETS="yt_channel_analyzer.test_discovery yt_channel_analyzer.test_extractor yt_channel_analyzer.test_transcripts_fetch"
TARGETS="${RALPH_VERIFY_TARGETS:-$DEFAULT_TARGETS}"

if [[ $# -gt 0 ]]; then
  TARGETS="$*"
fi

exec python3 -m unittest -q ${TARGETS}
