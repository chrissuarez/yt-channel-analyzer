# Issue 11 — Channel Overview panel

Roadmap sections: §A6

## Why this slice exists

`GUI_UX_PLAN.md` defines five top-level GUI sections; **Section 1 — Channel
Overview** is the only one that is fully missing. The plan calls for a
top-of-page panel that orients the operator: which channel are we looking
at, how much data exists, and is there a recent discovery run to review.

The other plan gaps (run-ID demote = Priority 2; transcript-aware comparison
readiness = Priority 4) are out of scope for this slice. They will be filed
as separate issues after this lands.

## Scope

Implement the three checkboxes under §A6 in ROADMAP.md, in order:

1. Backend `_build_channel_overview` helper + `channel_overview` payload key
   on `/api/state` + payload-shape test.
2. Channel Overview HTML panel rendered above the Discovery Topic Map +
   empty-state copy + HTML wiring test.
3. Polish: graceful no-primary-channel state, empty-DB safety, `UI_REVISION`
   bump if JS shape changed. Final iteration emits COMPLETE.

## Acceptance criteria

- New `channel_overview` key on the `/api/state` JSON payload includes:
  channel title, channel id, video count, transcript count, distinct topic
  count (topics with ≥1 `video_topics` row scoped to the channel), distinct
  subtopic count (same shape via `video_subtopics`), comparison group count,
  and a `latest_discovery` object with `id`, `status`, `started_at`, `model`,
  `prompt_version` (or `null` when no run exists).
- Top-of-page Channel Overview panel renders before the Discovery Topic Map
  and shows: channel title + id in the header, stat tiles for the counts
  above, and a "Latest discovery" block with the run metadata or the empty
  copy "No discovery yet — run `analyze` or `discover` to start."
- Panel renders without errors when `primary_channel` is unset, when the DB
  is empty, and when `discovery_runs` is empty.
- Tests: at least one payload-shape test (counts match a seeded
  stub-discovery fixture; empty-DB shape returns `latest_discovery: null`)
  and at least one HTML wiring test (panel + tiles render with seeded
  values; empty-state copy appears when no run).

## Out of scope

- Run-ID demotion / Advanced-history move (separate slice).
- Transcript / processed coverage on comparison readiness (separate slice).
- Real-LLM smoke (no paid run; this is review_ui.py + db.py reads only).
- Migrations or schema changes — every count is computable from existing
  tables (`videos`, `video_transcripts`, `video_topics`, `video_subtopics`,
  `topics`, `subtopics`, `comparison_groups`, `discovery_runs`).
- Visual polish beyond functional stat tiles + empty state. No design system,
  no extra CSS hierarchy beyond mirroring existing panel patterns.

## Agent notes

- Existing channel-context surface lives near `review_ui.py` line 990
  (`context.channel_title`, `context.channel_id`). The overview panel should
  reuse that data rather than fetching channel metadata twice.
- `get_primary_channel(db_path)` returns the channel scope; if the result is
  `None`, render an empty Channel Overview header with a "No primary
  channel set" hint instead of crashing.
- `discovery_runs` already has the columns this slice needs — no schema
  change required. `MAX(id)` scoped by `channel_id` (or by `project_id` if
  channel scoping isn't denormalized on that table — verify before writing
  the query) returns the latest run.
- The state payload is large. Add the new key alongside the existing keys;
  do not refactor the payload assembler.
- HTML pattern: existing panels use `<section class="panel">` with a
  header + body div. Match that pattern; do not introduce a new panel
  class hierarchy.
- The plan calls out a "comparison group count" tile. Comparison groups
  are now legacy (`§A4`). Surface the count as-is — it's a real number
  the DB still maintains — but don't add comparison-group call-to-action
  buttons. Keep the panel discovery-first.
- Per-iteration verify gate excludes `test_transcripts` by default; if a
  test is added there, override `RALPH_VERIFY_TARGETS` for that iteration.
- HITL pause if iteration 2's `review_ui.py` HTML changes exceed
  ~300 lines (per Q4 baseline trigger). The full panel + render JS should
  fit comfortably under that.
