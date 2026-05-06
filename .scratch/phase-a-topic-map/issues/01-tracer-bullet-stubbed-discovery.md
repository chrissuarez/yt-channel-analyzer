# 01 — Tracer bullet: end-to-end topic map with stubbed LLM

Status: needs-triage
Type: HITL
User stories covered: 1, 3, 18, 22 (from `PRD_PHASE_A_TOPIC_MAP.md`)
Roadmap sections: §A1, §A2, §A3, §A4

## Parent

[`PRD_PHASE_A_TOPIC_MAP.md`](../../../PRD_PHASE_A_TOPIC_MAP.md)

## What to build

Get a thin end-to-end wire from CLI through schema through GUI working, using a hardcoded fake LLM response. No real LLM call yet — the goal is to prove the pipeline shape and lock the schema before LLM work goes in.

The user runs `analyze <channel>` (or equivalent), the system ingests channel + episode metadata, runs a stub "discovery" that returns a hardcoded taxonomy (e.g. 3 topics, no subtopics, single-topic per episode, no confidence), persists it through the new junction tables, and the GUI renders a topic map view showing topic names with episode counts.

This slice also folds in two prerequisites that everything else depends on:
- **Schema migration** introducing `discovery_runs`, `video_topics` (and `video_subtopics` placeholder) junction tables, with a backfill that promotes existing primary/secondary topic data into `video_topics` as `source="manual"`, `confidence=1.0`.
- **Move comparison-group code to `legacy/`**: `comparison_group_suggestions.py`, `group_analysis.py`, group parts of `markdown_export.py`, full-transcript pipeline parts of `processing.py`. CLI commands for these still work but emit a deprecation notice. Comparison-group surface removed from primary GUI navigation.

## Acceptance criteria

- [ ] New tables exist: `discovery_runs`, `video_topics`, `video_subtopics`
- [ ] Migration backfills primary/secondary topic data into `video_topics` (`source="manual"`, `confidence=1.0`); idempotent (running twice doesn't double-insert)
- [ ] `discover` CLI command runs against a small fake channel fixture and produces a `discovery_runs` row + topic + assignment rows
- [ ] Discovery module's LLM call is a stub that returns a hardcoded JSON payload (real LLM in slice 02)
- [ ] GUI loads, comparison-group panels are gone from primary nav, new topic map view renders topics from the latest discovery run with episode counts
- [ ] Comparison-group code physically moved to `legacy/`; old CLI commands still importable (with deprecation warning); existing tests still pass after import path updates
- [ ] Smoke test: end-to-end run on a 10-episode SQLite fixture produces the expected topic count in the GUI's `/api/state` payload

## Why HITL

This slice locks the schema shape and the wire-end-to-end UI patterns that everything from 02–09 builds on. Worth a brief design check before LLM work goes in.

## Blocked by

None — can start immediately.
