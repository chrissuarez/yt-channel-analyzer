# 06 — Curation actions: rename + merge

Status: needs-triage
Type: AFK
User stories covered: 9, 10
Roadmap sections: §A3

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

The user can rename a topic in place ("Wealth" → "Money") and merge two topics into one ("Wealth" + "Money" → "Money", with all episode assignments combined). Both are reachable from the topic map view. Backend endpoints persist the changes and refresh the GUI state.

Rename and merge operations are recorded as `source="manual"` curation deltas so slice 08 can preserve them across re-runs.

## Acceptance criteria

- [ ] GUI: rename topic action (inline edit or modal) on each topic card / detail view
- [ ] GUI: merge topic action (pick a target topic to merge into); confirms before applying
- [ ] Backend endpoints for rename and merge update the database transactionally
- [ ] Rename does not lose any episode assignments; merge consolidates duplicates (an episode in both source topics is deduplicated under the target)
- [ ] Curation actions recorded in a way that survives a discovery re-run (mechanism may be a `topic_curation_events` table or equivalent — to be designed in this slice)
- [ ] Tests cover: rename happy path; merge with overlapping episode assignments; merge of topics with different subtopic sets

## Blocked by

- Slice 02 (need real topics to curate)
