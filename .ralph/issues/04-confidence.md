# Issue 04 — Confidence + reason slice

User stories covered:
- As an editor, when I look at a topic-map episode card I want to see
  *why* the LLM placed the episode there and how confident it was, so I
  can decide whether to trust the assignment or curate it.

Roadmap sections: §A2

## Scope

Widen the discovery prompt + schema + payload so the LLM emits
`confidence` (0.0–1.0) and `reason` (short string) per assignment, and
those values flow through `_payload_from_response` into the existing
`video_topics` / `video_subtopics` rows.

Slices 02 + 03 already shipped:
- the schema and prompt for broad topics + per-assignment subtopics
- the persistence columns (`confidence`, `reason` are already written
  on both `video_topics` and `video_subtopics`)
- the GUI payload + episode-card rendering of `confidence` and `reason`
  (covered by `DiscoveryLowConfidenceThresholdTests` and the
  fixture-driven topic-map render tests)

What's missing is the LLM emission path: today
`_payload_from_response` hardcodes `confidence=1.0` and `reason=""`
regardless of what the model returned. This slice wires the model's
own values through.

Multi-topic membership stays deferred to slice 05.

## Acceptance criteria

- `_DISCOVERY_SCHEMA` assignment item requires `confidence` (number,
  `minimum: 0`, `maximum: 1`) and `reason` (string, `minLength: 1`).
  Schema stays `additionalProperties: false` everywhere.
- `_DISCOVERY_SYSTEM` prompt asks for `confidence` (0.0–1.0) and a
  short `reason` per assignment, with the example JSON in the system
  message updated to include both fields.
- `DISCOVERY_PROMPT_VERSION` bumped to `discovery-v3`.
- `_payload_from_response` reads `confidence` and `reason` from each
  assignment item; the prior `1.0` / `""` literals are gone.
- `stub_llm` keeps `confidence=1.0` (so existing fixtures stay valid)
  but ships a non-trivial `reason` value (e.g. `"stub assignment"` —
  matches the existing string in `discovery.py:410`, just no longer
  contradicted by `_payload_from_response`).
- Tests added or amended under `test_discovery.py`:
  - existing `test_schema_rejects_assignment_with_extra_keys` is
    re-pointed: `confidence` is now valid, so swap to a still-unknown
    key (e.g. `priority` or `weight`) so the rejection assertion still
    fires
  - new positive test: schema accepts an assignment carrying
    `confidence` + `reason`, rejects one missing either, rejects
    `confidence` outside [0, 1] and empty `reason`
  - new round-trip test (mirrors
    `test_callable_round_trips_payload_via_extractor`) that puts a
    non-1.0 confidence + non-empty reason on the FakeLLMRunner response
    and asserts both flow into `DiscoveryAssignment`
  - a persistence test asserts that a varied-confidence payload writes
    the model-emitted values to `video_topics.confidence/reason` (not
    1.0 / "")
- ROADMAP §A2 lines 72/74 amended with a "Slice 04 scope" postscript
  in the same style as the existing slice 02/03 postscripts. The two
  fresh §A2 unchecked boxes (slice 04 schema + prompt; slice 04
  payload threading) get ticked.
- WORKLOG entry per Ralph iteration.
- Verify gate green (`test_discovery + test_extractor`).

## Out of scope

- Real-LLM smoke validating the new prompt produces sensible reasons
  on a real channel — re-homed to §A5 / issue 10 (same pattern as
  issue 03 criterion 6, since smoke is HITL by Q4 spec).
- Multi-topic membership (slice 05).
- Any GUI changes — the topic-map JS already renders both fields
  from `_build_discovery_topic_map`'s payload.

## Notes for the agent

- The two unchecked §A2 boxes are the iteration unit. Either one is a
  reasonable iter 1 (both should be doable in a single iteration if
  the diff stays small, but err on the side of two iterations for a
  cleaner per-iteration commit).
- The `DiscoveryAssignment` dataclass already declares `confidence:
  float` and `reason: str` — no dataclass change needed. Same for
  persistence INSERTs (already write both columns).
- Existing test at `test_discovery.py:3314`
  (`test_schema_rejects_assignment_with_extra_keys`) deliberately uses
  `confidence` as the rejected key with an explicit comment "confidence
  ships in slice 04". When it ships, swap the rejected key to one
  still outside the schema. Don't delete the test.
- The slice 02 smoke recipe at `.scratch/issue-02/smoke.py` is the
  fastest sanity check that the new prompt version still parses on
  Haiku — but **don't run it from this AFK loop** (real-LLM HITL
  trigger). Note in the WORKLOG that smoke is recommended pre-merge.
- Bump the example JSON shown in `_DISCOVERY_SYSTEM` to include
  `"confidence": 0.85, "reason": "..."` on the assignment example —
  Haiku is sensitive to example shape (slice-02 lesson: model wraps
  JSON in fences if not shown an unfenced example).
