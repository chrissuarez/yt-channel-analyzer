# 04 — Confidence + reason on each assignment

Status: needs-triage
Type: AFK
User stories covered: 6, 7
Roadmap sections: §A2, §A3

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

Each `video_topics` and `video_subtopics` row carries a `confidence` (0.0–1.0) and a short `reason` string explaining why the episode landed there ("matched chapter title 'Gut Microbiome'", "title contains 'sleep'"). The discovery prompt elicits both. The GUI renders confidence as a numeric or visual indicator on each episode card, plus the reason inline.

This slice does not yet apply faded styling to low-confidence cards — that's slice 09. Here we just persist and display.

## Acceptance criteria

- [ ] `video_topics.confidence`, `video_topics.reason`, `video_subtopics.confidence`, `video_subtopics.reason` columns added (migration is non-destructive)
- [ ] Discovery prompt updated to ask the model for confidence + reason per assignment
- [ ] Validation rejects assignments missing confidence or reason
- [ ] Episode cards in the GUI show confidence + reason for each topic/subtopic the episode is in
- [ ] Smoke test: assignments come through with reasonable-looking reason strings on a real-channel run

## Blocked by

- Slice 02 (depends on real discovery being live; may be sequenced before or after slices 03/05/06 — touches the same area)
