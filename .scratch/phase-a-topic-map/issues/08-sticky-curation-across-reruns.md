# 08 — Sticky curation across discovery re-runs

Status: needs-triage
Type: AFK
User stories covered: 16, 17

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

When the user re-runs discovery on a channel (e.g. after new episodes are added), their curation work — renames, merges, splits, episode moves, marked-wrong assignments — must survive. The new run produces a candidate diff against the curated state; the user reviews and approves additions/changes rather than getting silently overwritten.

Mechanism: the curation events from slices 06 and 07 are replayed against the new discovery output before the new state is committed.

## Acceptance criteria

- [ ] Re-running discovery does NOT silently overwrite renames, merges, splits, moves, or marked-wrong assignments
- [ ] The new discovery run lands in a "pending" state alongside the curated state; the user sees a diff (new topics added, new episodes assigned, conflicts requiring decision)
- [ ] User can approve the diff in whole or per-change; rejected changes are preserved as curation events
- [ ] Test: rename topic A → B, re-run discovery (where mock LLM still emits "A"); after re-run, the topic still appears as "B" with all episodes intact
- [ ] Test: mark assignment wrong, re-run discovery (mock still proposes the same assignment); the marked-wrong assignment does not reappear silently
- [ ] Test: discovery proposes a brand-new topic the curated state has never seen; it appears in the diff for user approval

## Blocked by

- Slice 06 (rename, merge curation events)
- Slice 07 (split, move, mark-wrong curation events)
