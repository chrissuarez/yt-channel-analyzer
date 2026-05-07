# 03 — Subtopics in discovery + GUI drill-down

Status: done (criteria 1-5); criterion 6 re-homed under §A5 / issue 10
Type: AFK
User stories covered: 4, 5
Roadmap sections: §A2, §A3

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

Extend discovery to also propose subtopics under each broad topic, and assign episodes to subtopics. The GUI gains a drill-down: click a topic → see its subtopics with episode counts → click a subtopic → see assigned episodes.

## Acceptance criteria

- [ ] Prompt extended to elicit subtopics under each broad topic, plus per-episode subtopic assignments
- [ ] Response validation extended for the new shape
- [ ] `video_subtopics` rows persisted alongside `video_topics`
- [ ] GUI: topic map view drills into a topic detail view that lists subtopics with counts; clicking a subtopic shows the assigned episode list
- [ ] Episodes can have a topic but no subtopic (display them as "unassigned within topic")
- [ ] Smoke test: a small real channel produces topics with at least 2 subtopics each *(re-homed: validated under §A5 / issue 10's first real DOAC run rather than as a standalone slice-03 smoke)*

## Blocked by

- Slice 02 (depends on real LLM discovery to extend the prompt)
