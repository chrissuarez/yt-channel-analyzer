# Slice 05 overlay — multi-topic episode membership

Roadmap sections: §A2, §A3

PRD reference: `.scratch/phase-a-topic-map/issues/05-multi-topic-membership.md`

## Scope

Make discovery treat a single episode belonging to multiple topics as a
first-class shape — both in the LLM payload (multiple `assignments`
entries per `youtube_video_id`) and in the GUI ("also in: <topics>" pill
on each episode card). The schema and persistence already support N rows
per video in `video_topics`; the blocker is the prompt rule explicitly
forbidding it and the GUI not surfacing it.

## Acceptance criteria (mapped to ROADMAP boxes)

The two unchecked §A2/§A3 sub-bullets added to ROADMAP for this slice
are the iteration units:

1. **§A2 box** — prompt v4 + multi-topic stub fixture (criteria 1, 2, 5
   from the parent issue file). Relax `_DISCOVERY_SYSTEM`'s "Every
   supplied episode must appear exactly once in `assignments`" rule;
   replace with explicit guidance that an episode *may* have multiple
   `assignments` entries with different `topic` values when it
   genuinely covers each, with anti-over-tagging language ("only when
   the episode meaningfully covers each — secondary topics should be
   the exception, not the default"). Bump `DISCOVERY_PROMPT_VERSION`
   to `discovery-v4`. Extend `stub_llm` so at least one stub video
   carries two `DiscoveryAssignment` entries (different `topic_name`)
   so the GUI smoke + tests can verify multi-topic without paying for
   an LLM. **No schema change** — `_DISCOVERY_SCHEMA` already permits
   multiple `assignments` entries with the same `youtube_video_id`.

2. **§A3 box** — episode card "also in" pill (criteria 3, 4 from the
   parent issue file). `_build_discovery_topic_map` extends each
   per-topic episode dict with `also_in: [<topic_name>, ...]` listing
   the *other* topics that video appears under in this run; the JS
   `renderDiscoveryEpisodeItem` shows an inline pill ("also in: Topic
   B, Topic C") only when `also_in` is non-empty. The episode already
   appears under each topic's list because the query is per-row on
   `video_topics`, so criterion 3 ("appears in both lists") is
   covered by virtue of the multi-row stub fixture from box 1.

3. **Loose-end tests + COMPLETE** (no separate ROADMAP box, but
   needed before COMPLETE):
   - Round-trip test: stub run persists ≥2 `video_topics` rows for
     the multi-topic stub video.
   - HTML/payload test asserting `also_in` is populated for the
     multi-topic episode and empty for single-topic episodes.
   - Search `test_discovery.py` and `test_review_ui.py` for any
     existing assertion that breaks under the new fixture (e.g.
     `len(assignments) == len(videos)`); update or relax as
     appropriate.

## Out of scope (explicit)

- **No real-LLM smoke for slice 05.** The §A5 paid run on
  `2026-05-07` already validated the LLM emits credible topics +
  subtopics + confidence + reasons on real channel data (evidence:
  `.scratch/issue-10/doac-smoke-20260507-221241.log`). Whether real
  Haiku 4.5 emits multi-topic assignments under v4 prompt is a
  passive observation that will surface in any subsequent paid run;
  it is *not* a slice-05 blocker. Do not propose another paid run.
- **No "also in" rendering changes for the subtopic drill-downs**
  inside `<details>` buckets — primary topic-list cards only. If a
  follow-up wants subtopic-level "also in", file separately.
- **No backward-compatibility shim** for `discovery-v3` — bump the
  version cleanly and update prompt-version-bound tests.

## Agent notes / known gotchas

- The blocker line is in `discovery._DISCOVERY_SYSTEM` (currently
  around line 219): `"- Every supplied episode must appear exactly
  once in `assignments`.\n"`. Replace, don't append — leaving the
  old rule alongside the new permission produces contradictory
  guidance for the model.
- `_payload_from_response` and the persistence loop in
  `run_discovery` already handle N rows per video correctly — each
  assignment becomes its own `video_topics` row keyed by `(video_id,
  topic_id)`. No code change needed there. Verify by reading the
  loop, don't rewrite it defensively.
- `stub_llm` ships in `discovery.py` (around line 407). Tests likely
  count `len(assignments) == len(videos)` — search
  `test_discovery.py` for `len(.*assignments)` and re-point
  expectations *before* the stub change lands so the verify gate
  doesn't flap red mid-iteration.
- `_DISCOVERY_SCHEMA["properties"]["assignments"]["items"]["required"]`
  does **not** uniqueness-constrain `youtube_video_id` — confirm by
  reading `_DISCOVERY_SCHEMA` (around line 234). A schema change for
  this slice would be a smell; leave it alone.
- The current discovery-v3 example JSON in `_DISCOVERY_SYSTEM`
  shows one assignments entry — extend it to show one video with
  two entries (different topics) so the model has an in-prompt
  template for the new shape.
- Each episode card under a given topic should show the *other*
  topics, not all topics including itself. Filter `topic_name !=
  current_topic_name` when building `also_in`.
- The "also in" pill should follow the existing inline-meta styling
  pattern in `.discovery-episode-meta` — don't introduce a new CSS
  class hierarchy unless the pill has visually distinct needs.

## Verification

The verify gate (`test_discovery + test_extractor`) should stay
green at every iteration. With the round-trip test added in box 3,
expected count post-slice is ≥168 (current main is 167).
