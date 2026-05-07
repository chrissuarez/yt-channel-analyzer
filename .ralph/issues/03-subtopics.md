# Issue 03 — Subtopics slice

User stories covered:
- As an editor, when I run discovery I want the LLM to surface subtopics
  beneath each broad topic so the topic map can drill down beyond a single
  level.

Roadmap sections: §A2

## Scope

Widen the discovery prompt + schema + persistence to emit and store
subtopics. UI work (§A3 line 80 "topic detail: subtopics + episodes") is
explicitly deferred — once this slice lands, that line becomes
implementable as a follow-up.

Slice 02 produced broad topics + per-episode single-topic assignments.
Slice 03 adds:
- `subtopics: [{name, parent_topic}]` on the LLM payload
- `subtopic` field on each assignment (referencing a name from
  `subtopics` whose `parent_topic` matches the assignment's `topic`)
- persistence into the existing `subtopics` and `video_subtopics`
  tables (junction shape already shipped in §A1)

Confidence/reason on assignments and multi-topic membership remain
deferred to slices 04 and 05 respectively. `_DISCOVERY_SCHEMA` should
still be `additionalProperties: false` everywhere — slice 04/05 will
relax further.

## Acceptance criteria

- `_DISCOVERY_SCHEMA` accepts `subtopics` array (each item: `name` str,
  `parent_topic` str) and `subtopic` field on assignment items. Schema
  remains strict (`additionalProperties: false`).
- Discovery system prompt asks for 2-6 subtopics per topic, names a
  subtopic per assignment, and forbids prose/fences.
- `DiscoveryPayload` carries a `subtopics: list[DiscoverySubtopic]` and
  each `DiscoveryAssignment` carries `subtopic_name: str | None`.
- `run_discovery` persistence:
  - Inserts `subtopics` rows under their parent topic (idempotent on
    `(topic_id, name)` per existing schema)
  - Inserts `video_subtopics` rows linking video → subtopic with
    `assignment_source='auto'`, `discovery_run_id=run_id`
  - When an assignment's `subtopic` is null/missing, no
    `video_subtopics` row is written for that video (graceful)
  - Validates `parent_topic` references a known topic; raises
    `ValueError` like the existing topic-name validator if not
- `stub_llm` returns one subtopic per video (`"General / sub"` or
  similar) so the existing single-topic stub remains exercised plus
  new subtopic paths.
- Tests added under `test_discovery.py`:
  - schema accepts payload with subtopics, rejects payload with extra
    keys on subtopic items
  - persistence writes expected `subtopics` rows
  - persistence writes expected `video_subtopics` rows
  - missing `subtopic` on an assignment skips the junction row
  - parent_topic referencing unknown topic raises `ValueError`
- ROADMAP §A2 lines 72/74 updated to reflect subtopics now shipped.
  `slice 02 scope` notes amended to `slice 02-03 scope` (or a fresh
  `slice 03 scope` postscript).
- WORKLOG entry per Ralph iteration.
- Verify gate green (`test_discovery + test_extractor`).

## Out of scope

- §A3 UI for subtopic detail (separate follow-up after this slice)
- Confidence + reason on assignments (slice 04)
- Multi-topic membership (slice 05)
- Real-LLM smoke (slice 02's smoke recipe still works for sanity
  checks but isn't part of this slice's acceptance)

## Notes for the agent

- The `subtopics` and `video_subtopics` tables already exist (§A1
  shipped them in commit history pre-Ralph). Confirm column names with
  `db.py`/`schema.sql` before writing INSERTs — don't assume.
- The slice-02 prompt-version constant (`DISCOVERY_PROMPT_VERSION`)
  should bump because the schema is changing. Check what the existing
  version is and increment.
- When updating `_payload_from_response`, mirror the assignment loop's
  shape — keep `confidence=1.0`, `reason=""` defaults until slice 04.
