# 07 — Curation actions: split + move episode + mark wrong

Status: needs-triage
Type: AFK
User stories covered: 11, 12, 13

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

Three additional curation actions:

- **Split** an overcrowded topic into two or more topics. The user picks which episodes go into which new topic.
- **Move** an episode between subtopics within the same parent topic.
- **Mark assignment wrong** — flags an episode-to-topic or episode-to-subtopic assignment as incorrect. The assignment is removed (or hidden) and the action is recorded for later use as training/refinement signal.

All three actions reuse the curation-event mechanism introduced in slice 06 so they survive discovery re-runs (handled in slice 08).

## Acceptance criteria

- [ ] GUI: split topic action with episode-picker UI to redistribute
- [ ] GUI: move episode between subtopics (drag, dropdown, or button — implementer's choice)
- [ ] GUI: mark assignment wrong action on each episode card (small "wrong topic?" affordance)
- [ ] Backend endpoints for each action persist transactionally
- [ ] Marked-wrong assignments removed from the displayed assignment list; recorded as curation events
- [ ] Tests cover: split with non-overlapping episode subsets; move episode within parent topic; mark-wrong removal

## Blocked by

- Slice 03 (move-between-subtopics needs subtopics to exist)
- Slice 06 (curation-event mechanism)
