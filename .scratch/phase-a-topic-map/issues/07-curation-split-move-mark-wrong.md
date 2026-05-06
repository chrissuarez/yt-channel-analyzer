# 07 — Curation actions: split + move episode + mark wrong

Status: ready-for-human
Type: AFK
User stories covered: 11, 12, 13
Roadmap sections: §A3

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

Three additional curation actions:

- **Split** an overcrowded topic into two or more topics. The user picks which episodes go into which new topic.
- **Move** an episode between subtopics within the same parent topic.
- **Mark assignment wrong** — flags an episode-to-topic or episode-to-subtopic assignment as incorrect. The assignment is removed (or hidden) and the action is recorded for later use as training/refinement signal.

All three actions reuse the curation-event mechanism introduced in slice 06 so they survive discovery re-runs (handled in slice 08).

## Acceptance criteria

- [x] GUI: split topic action with episode-picker UI to redistribute (Ralph iteration 10)
- [x] GUI: move episode between subtopics (drag, dropdown, or button — implementer's choice) (Ralph iteration 11)
- [x] GUI: mark assignment wrong action on each episode card (small "wrong topic?" affordance) (Ralph iteration 12)
- [x] Backend endpoints for each action persist transactionally (`/api/discovery/topic/split`, `/api/discovery/episode/move-subtopic`, `/api/discovery/episode/mark-wrong`)
- [x] Marked-wrong assignments removed from the displayed assignment list; recorded as curation events (`wrong_assignments` table, Ralph iteration 12)
- [x] Tests cover: split with non-overlapping episode subsets; move episode within parent topic; mark-wrong removal (`test_discovery.py`)

## Blocked by

- Slice 03 (move-between-subtopics needs subtopics to exist)
- Slice 06 (curation-event mechanism)
