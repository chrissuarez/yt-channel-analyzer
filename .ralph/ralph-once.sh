#!/usr/bin/env bash
# HITL Ralph: run a single iteration. Infers issue # from the current branch
# (feat/issue-NN-...), renders .ralph/PROMPT.md with substitutions, concatenates
# .ralph/issues/<NN>-overlay.md if present, and invokes `claude -p`.
#
# Use this when you want to watch one iteration, intervene, and re-run by
# hand. For unattended runs use afk-ralph.sh.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ ! "$CURRENT_BRANCH" =~ ^feat/issue-([0-9]+[a-z]?)- ]]; then
  echo "ralph-once: current branch '$CURRENT_BRANCH' does not match 'feat/issue-NN-...'" >&2
  exit 2
fi
ISSUE_NUM="${BASH_REMATCH[1]}"
BRANCH="$CURRENT_BRANCH"

PROMPT_TEMPLATE=".ralph/PROMPT.md"
OVERLAY=".ralph/issues/${ISSUE_NUM}-overlay.md"

if [[ ! -f "$PROMPT_TEMPLATE" ]]; then
  echo "ralph-once: missing $PROMPT_TEMPLATE" >&2
  exit 2
fi

RENDERED="$(mktemp -t ralph-prompt.XXXXXX.md)"
trap 'rm -f "$RENDERED"' EXIT

sed -e "s|{{ISSUE_NUM}}|${ISSUE_NUM}|g" \
    -e "s|{{BRANCH}}|${BRANCH}|g" \
    -e "s|{{ITER}}|HITL|g" \
    "$PROMPT_TEMPLATE" > "$RENDERED"

if [[ -f "$OVERLAY" ]]; then
  printf '\n\n## Issue-specific overlay\n\n' >> "$RENDERED"
  cat "$OVERLAY" >> "$RENDERED"
  echo "ralph-once: applied overlay $OVERLAY"
fi

claude \
  --permission-mode acceptEdits \
  -p "@${RENDERED} Run one Ralph iteration as specified in the rendered prompt above. End your response with the appropriate <ralph>...</ralph> status sigil."
