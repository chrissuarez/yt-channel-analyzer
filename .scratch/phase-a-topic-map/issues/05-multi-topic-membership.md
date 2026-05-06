# 05 — Multi-topic episode membership end-to-end

Status: needs-triage
Type: AFK
User stories covered: 8
Roadmap sections: §A1, §A2, §A3

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

Discovery output explicitly allows an episode to belong to N topics (and N subtopics within those). The schema already supports this from slice 01; this slice ensures the prompt elicits multi-topic assignments where genuinely warranted, and the GUI shows the same episode under every topic it belongs to.

The episode card under a given topic should still indicate "also in: <other topics>" so the user sees the multi-topic nature.

## Acceptance criteria

- [ ] Prompt explicitly permits multi-topic assignment, with guidance to do so only when the episode genuinely covers multiple topics (avoid over-tagging)
- [ ] Validation accepts multiple topic entries per episode
- [ ] GUI: an episode assigned to topics A and B appears in both topic A's episode list and topic B's episode list
- [ ] Each episode card surfaces the other topics it belongs to (small inline pill / "also in" line)
- [ ] Smoke test fixture includes at least one episode with multi-topic assignment; UI displays it under each

## Blocked by

- Slice 02 (multi-topic prompt and validation depend on real discovery)
