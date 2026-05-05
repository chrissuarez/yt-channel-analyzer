# 09 — Sort options + low-confidence visual styling

Status: needs-triage
Type: AFK
User stories covered: 14, 15

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

The episode list within a subtopic (or topic, when no subtopic is selected) gains sort options: by recency, by view count, by confidence. Default sort is recency.

Episode cards with low-confidence assignments (below a configurable threshold, e.g. 0.5) are visually de-emphasised — faded styling, muted colours, or a "low confidence" badge. The user can spot which assignments to review first.

## Acceptance criteria

- [ ] Sort dropdown on episode lists with options: recency, view count, confidence (high → low)
- [ ] Default sort persists per topic between sessions, or resets to recency — implementer's choice, document the decision
- [ ] Low-confidence assignments (confidence < threshold) render visually distinct (faded, muted, badge — pick one)
- [ ] Threshold is configurable (config file or env var; default 0.5)
- [ ] Test: a fixture with mixed-confidence assignments renders the low-confidence ones with the distinct style

## Blocked by

- Slice 04 (need confidence values to sort by and to drive styling)
