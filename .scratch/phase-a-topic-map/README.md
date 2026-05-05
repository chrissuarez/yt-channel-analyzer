# Phase A — Topic Map MVP

Feature directory for the Phase A build slice.

- **PRD:** [`../../PRD_PHASE_A_TOPIC_MAP.md`](../../PRD_PHASE_A_TOPIC_MAP.md) (lives at the project root because it's cross-cutting)
- **Issues:** [`./issues/`](./issues/) — vertical slices, numbered in dependency order

## Slice overview

| # | Title | Type | Blocked by |
|---|---|---|---|
| 00 | Build the Extractor Module (prerequisite for all LLM work) | AFK | — |
| 01 | Tracer bullet — end-to-end topic map with stubbed LLM | HITL | — |
| 02 | Real LLM discovery replaces the stub | AFK | 00, 01 |
| 03 | Subtopics in discovery + GUI drill-down | AFK | 02 |
| 04 | Confidence + reason on each assignment | AFK | 02 |
| 05 | Multi-topic episode membership end-to-end | AFK | 02 |
| 06 | Curation actions — rename + merge | AFK | 02 |
| 07 | Curation actions — split + move episode + mark wrong | AFK | 03, 06 |
| 08 | Sticky curation across discovery re-runs | AFK | 06, 07 |
| 09 | Sort options + low-confidence visual styling | AFK | 04 |
| 10 | First real run on Diary of a CEO + cheatsheet update | HITL | all |

All issues start with `Status: needs-triage`. Triage moves them to `ready-for-agent` (AFK-pickable) or `ready-for-human`.
