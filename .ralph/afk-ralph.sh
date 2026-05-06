#!/usr/bin/env bash
# AFK Ralph: capped, unattended loop with live tool/text streaming and a
# script-layer verify gate.
#
# Pre-flight (refuses to start otherwise):
#   - branch matches feat/issue-NN-...
#   - working tree is clean (`git status --porcelain` empty)
#   - verify gate is currently green
#
# Per iteration:
#   - tags ralph/iter-N-pre-<ts> BEFORE invoking claude (rollback handle)
#   - renders .ralph/PROMPT.md + optional .ralph/issues/<NN>-overlay.md
#   - runs `claude -p --output-format stream-json --verbose`; tees the full
#     stream to .ralph/logs/iter-N-<ts>.log and surfaces a filtered live view
#     of assistant text + tool calls
#   - parses the last <ralph>...</ralph> sigil from the log
#   - on CONTINUE: re-runs the verify gate; a red gate aborts the loop even
#     if the agent claimed success
#
# Recognised sigils: COMPLETE / HITL_PAUSE / BLOCKED / BRANCH_MISMATCH /
# CONTINUE.
#
# Tunables:
#   MAX_ITER (default 8)            iteration cap; loop exits earlier on
#                                   COMPLETE / HITL_PAUSE / BLOCKED / red gate
#   RALPH_VERIFY_TARGETS            unittest targets, space-separated
#                                   (default: discovery + extractor; see Q2 spec)
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

MAX_ITER="${MAX_ITER:-8}"
LOG_DIR=".ralph/logs"
mkdir -p "$LOG_DIR"

RALPH_VERIFY_TARGETS="${RALPH_VERIFY_TARGETS:-yt_channel_analyzer.test_discovery yt_channel_analyzer.test_extractor}"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ ! "$CURRENT_BRANCH" =~ ^feat/issue-([0-9]+[a-z]?)- ]]; then
  echo "afk-ralph: current branch '$CURRENT_BRANCH' does not match 'feat/issue-NN-...'" >&2
  exit 2
fi
ISSUE_NUM="${BASH_REMATCH[1]}"
BRANCH="$CURRENT_BRANCH"
echo "afk-ralph: issue=${ISSUE_NUM} branch=${BRANCH} max_iter=${MAX_ITER}"
echo "afk-ralph: verify targets = ${RALPH_VERIFY_TARGETS}"

PROMPT_TEMPLATE=".ralph/PROMPT.md"
OVERLAY=".ralph/issues/${ISSUE_NUM}-overlay.md"

if [[ ! -f "$PROMPT_TEMPLATE" ]]; then
  echo "afk-ralph: missing $PROMPT_TEMPLATE" >&2
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "afk-ralph: jq is required for stream filtering" >&2
  exit 2
fi

# Pre-flight 1: working tree must be clean. Per Q5 spec — protects unrelated
# in-progress work from being clobbered if Ralph stages broadly.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "afk-ralph: working tree is dirty. Commit or stash before AFK runs." >&2
  git status --short >&2
  exit 2
fi

# jq filter: emit one short line per assistant text chunk and per tool_use
# event. Read raw lines so a malformed line is silently skipped instead of
# aborting the stream and SIGPIPE-ing the claude process.
JQ_FILTER='
  inputs |
  try (
    fromjson |
    if .type == "assistant" and (.message.content | type) == "array" then
      .message.content[] |
      if .type == "text" then
        "[text] " + ((.text // "") | gsub("\n"; " ") | .[0:200])
      elif .type == "tool_use" then
        "[tool] " + .name + " :: " + (
          if .name == "Bash" then ((.input.command // "") | gsub("\n"; " ") | .[0:80])
          elif .name == "Edit" or .name == "Write" or .name == "Read" then (.input.file_path // "")
          elif .name == "Glob" or .name == "Grep" then (.input.pattern // "")
          else ""
          end
        )
      else empty
      end
    else empty
    end
  ) catch empty
'

render_prompt() {
  local out="$1"
  local iter="$2"
  sed -e "s|{{ISSUE_NUM}}|${ISSUE_NUM}|g" \
      -e "s|{{BRANCH}}|${BRANCH}|g" \
      -e "s|{{ITER}}|${iter}|g" \
      "$PROMPT_TEMPLATE" > "$out"
  if [[ -f "$OVERLAY" ]]; then
    printf '\n\n## Issue-specific overlay\n\n' >> "$out"
    cat "$OVERLAY" >> "$out"
  fi
}

verify_gate() {
  echo "afk-ralph: verify gate — running unittest (${RALPH_VERIFY_TARGETS})"
  ( cd .. && python3 -m unittest -q ${RALPH_VERIFY_TARGETS} )
}

# Pre-flight 2: verify gate must be green before iteration 1.
if ! verify_gate; then
  echo "afk-ralph: pre-flight verify gate is RED. Fix before AFK runs." >&2
  exit 1
fi
echo "afk-ralph: pre-flight verify gate green."

for ((i = 1; i <= MAX_ITER; i++)); do
  TS="$(date +%Y%m%d-%H%M%S)"
  LOG="$LOG_DIR/iter-${i}-${TS}.log"
  RENDERED="$(mktemp -t ralph-prompt.XXXXXX.md)"
  render_prompt "$RENDERED" "$i"

  # Per-iteration backup tag — local only, never pushed. Rollback handle.
  TAG="ralph/iter-${i}-pre-${TS}"
  git tag -- "$TAG" >/dev/null

  echo
  echo "=== AFK Ralph iteration $i / $MAX_ITER  ($TS) ==="
  echo "    log:  $LOG"
  echo "    tag:  $TAG  (rollback: git reset --hard $TAG)"
  if [[ -f "$OVERLAY" && $i -eq 1 ]]; then
    echo "    overlay: $OVERLAY"
  fi

  set +e
  claude \
    --permission-mode acceptEdits \
    --output-format stream-json \
    --verbose \
    -p "@${RENDERED} Run one Ralph iteration as specified in the rendered prompt above. End your response with the appropriate <ralph>...</ralph> status sigil." \
    | tee "$LOG" \
    | jq -nrR --unbuffered "$JQ_FILTER"
  PIPE_STATUS=("${PIPESTATUS[@]}")
  set -e
  rm -f "$RENDERED"

  if [[ "${PIPE_STATUS[0]}" -ne 0 ]]; then
    echo "afk-ralph: claude exited non-zero (${PIPE_STATUS[0]}). Stopping." >&2
    echo "    rollback: git reset --hard $TAG" >&2
    exit 1
  fi

  STATUS="$(grep -oE '<ralph>[A-Z_]+(: [^<]*)?</ralph>' "$LOG" | tail -n 1 || true)"
  echo "    status: ${STATUS:-<none>}"

  case "$STATUS" in
    *COMPLETE*)
      echo "afk-ralph: issue ${ISSUE_NUM} COMPLETE. Branch ready for review."
      exit 0
      ;;
    *HITL_PAUSE*)
      echo "afk-ralph: HITL_PAUSE — stopping for human review."
      exit 0
      ;;
    *BLOCKED*|*BRANCH_MISMATCH*)
      echo "afk-ralph: agent reported $STATUS — stopping." >&2
      echo "    rollback: git reset --hard $TAG" >&2
      exit 1
      ;;
    *CONTINUE*)
      if ! verify_gate; then
        echo "afk-ralph: agent emitted CONTINUE but verify gate is RED. Stopping." >&2
        echo "    rollback: git reset --hard $TAG" >&2
        exit 1
      fi
      ;;
    *)
      echo "afk-ralph: no recognised <ralph>...</ralph> sigil. Stopping for safety." >&2
      echo "    rollback: git reset --hard $TAG" >&2
      exit 1
      ;;
  esac
done

echo "afk-ralph: reached MAX_ITER=$MAX_ITER without completion. Stopping."
