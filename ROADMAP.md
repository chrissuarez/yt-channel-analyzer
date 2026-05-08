# YouTube Channel Analyzer — Roadmap

## Roadmap intent

This roadmap reflects the **2026-05-04 vision pivot**. The project is now a phased podcast knowledge extractor (see `PROJECT_SUMMARY.md`). This file answers:

- What is already done?
- What is the current phase and its concrete next steps?
- What is intentionally deferred?

Update this file when priorities change.

---

## Done / substantially present

### Foundation (carries over unchanged)
- [x] project-scoped SQLite DB, rerun-safe setup
- [x] single-channel MVP operating model
- [x] channel resolution from ID / handle / URL
- [x] channel and video metadata ingestion
- [x] limited-fetch support for testing

### Existing structure (carries over with repurposed semantics)
- [x] `topics`, `subtopics` tables and CRUD
- [x] primary + optional secondary topic on a video
- [x] review UI with suggest/review/approve/apply patterns
- [x] first-pass Topic Map view in the GUI (April 2026)
- [x] Approve + apply flows for topic and subtopic suggestions
- [x] selected-topic detail panel with assigned/unassigned inventory

### Existing AI suggestion machinery (will be repurposed)
- [x] broad-topic suggestion generation, review, approve, rename, bulk apply, supersede
- [x] subtopic suggestion generation with cluster-size threshold
- [x] run-scoped suggestion history

### Existing comparison-group machinery (moving to `legacy/`)
- [x] comparison group CRUD and membership management
- [x] comparison group suggestion logic
- [x] selective transcript fetching for chosen groups
- [x] deterministic transcript processing
- [x] group-level analysis
- [x] group markdown export

These are not deleted. They move to `legacy/` for archival reference and possible Phase C revival.

---

## Current active focus — Phase A: Topic Map MVP

See `PRD_PHASE_A_TOPIC_MAP.md` for the detailed plan.

The smallest version of the app that solves a real user problem ("which episodes are worth my time?") with no transcript spend.

### Phase A — concrete next steps

#### A1. Schema extension for multi-topic membership
- [x] Add `video_topics` junction table: `(video_id, topic_id, confidence, source, reason)` — extended existing table with `confidence`, `reason`, `discovery_run_id`; `assignment_source` CHECK now allows `'auto'`
- [x] Add `video_subtopics` junction table: `(video_id, subtopic_id, confidence, source, reason)` — same shape as `video_topics`
- [x] Add `discovery_runs` table to track Phase A runs (one per channel discovery pass)
- [x] Repair path: `_repair_video_topic_assignment_source_constraint` rebuilds old-shape junction tables whose CHECK lacks `'auto'` (rename → recreate → INSERT SELECT → drop)
- [ ] Migration that backfills existing primary/secondary topic data into the junction table
- [ ] Existing primary/secondary columns retained for backward compatibility but no longer authoritative

#### A2. Discovery module (`discovery.py`)
- [x] Module skeleton with `DiscoveryVideo`, `DiscoveryAssignment`, `DiscoveryPayload`, `run_discovery(...)` — LLM injected as a callable; persists run + topics + assignments
- [x] Stub LLM (`stub_llm` returns one topic "General" with every video assigned, confidence=1.0); constants `STUB_MODEL` / `STUB_PROMPT_VERSION`
- [x] CLI `discover --db-path --project-name --stub` wired to `run_discovery` (the `--stub` flag is required until the real LLM lands)
- [x] Pull all videos for the channel: title, description, chapter markers (Ralph iteration 1 — `Chapter` dataclass + `parse_chapters_from_description` in `discovery.py`; chapters parsed from description text using YouTube's own rules: ≥3 timestamps, first is 0:00, monotonically increasing; populated on `DiscoveryVideo.chapters` so the LLM callable receives them. No schema change — chapters are derived per run from the existing `videos.description` column.)
- [x] Pre-filter common boilerplate (sponsor reads, social calls-to-action) from descriptions (Ralph iteration 2 — `strip_description_boilerplate` in `discovery.py` drops sponsor-read and social-CTA lines via line-by-line regex; `_CHAPTER_LINE` matches are always preserved so episode structure still reaches the LLM. `run_discovery` applies it to `DiscoveryVideo.description` while `chapters` are still parsed from the original description.)
- [x] Build a single batched LLM call (Haiku 4.5 or GPT-4o-mini) (Ralph iteration 3 — `discovery.py` registers prompt `discovery.topics@discovery-v1` with system + JSON schema (`{topics: [str], assignments: [{youtube_video_id, topic}]}`, `additionalProperties: false`); `discovery_llm_via_extractor(extractor)` adapts an `Extractor.run_one` into the existing `LLMCallable` (one batched call per discovery run, all videos rendered into one prompt); `make_real_llm_callable(connection, *, model=None)` constructs an `AnthropicRunner` + `Extractor` wired adapter and raises unless `RALPH_ALLOW_REAL_LLM=1` is set, so the verify gate cannot accidentally spend tokens. Slice 02's confidence/reason fields stay default (1.0 / "") until later slices.)
- [x] Prompt produces: list of broad topics with subtopics, plus per-episode topic/subtopic assignments with confidence (0.0–1.0) and a short reason string *(slice 02 scope: prompt produces broad topics + per-episode single-topic assignments only — `_DISCOVERY_SCHEMA` is `additionalProperties: false`, so subtopics/confidence/reason are intentionally rejected here and ship deliberately in slices 03–05; shipped Ralph iteration 3, formalized iteration 4. Slice 03 scope: `_DISCOVERY_SCHEMA` widened to accept `subtopics: [{name, parent_topic}]` plus optional `subtopic` on each assignment; system prompt now asks for 2-6 subtopics per topic; `DISCOVERY_PROMPT_VERSION` bumped to `discovery-v2`. Confidence + reason still ship in slice 04. Issue 03 / Ralph iteration 1. Slice 04 scope: `_DISCOVERY_SCHEMA` assignment items now require `confidence` (number 0–1) + `reason` (string, `minLength: 1`); system prompt asks for both per assignment with the example JSON updated to include `"confidence": 0.85, "reason": "..."`; `DISCOVERY_PROMPT_VERSION` bumped to `discovery-v3`. `_payload_from_response` still hardcodes `1.0` / `""` until the next sub-plan threads them through. Issue 04 / Ralph iteration 1.)*
- [x] Validate response shape; reject malformed batches; retry once *(Extractor owns schema validation + one retry; Ralph iteration 5 — `discovery.py` `run_discovery` now wraps the `llm(videos)` call in try/except: on any exception it inserts a `discovery_runs` row with `status='error'`, commits, and re-raises. Acceptance "on second failure the run is marked errored and no partial state is persisted" — topic and `video_topics` writes only happen after a successful llm payload, so the error path leaves only the errored run row.)*
- [x] Persist to `topics`, `subtopics`, junction tables, `discovery_runs` *(slice 02 scope: `run_discovery` persists `topics` + `video_topics` + `discovery_runs` rows from the LLM payload (success and `status='error'` paths both audited). `subtopics` / `video_subtopics` intentionally stay empty in slice 02 — `_DISCOVERY_SCHEMA` is `additionalProperties: false` and rejects subtopic keys today by design; slices 03–05 widen the schema and the persistence handler. Ralph iteration 6. Slice 03 scope: `run_discovery` now persists `subtopics` (idempotent on `(topic_id, name)`) and `video_subtopics` rows with `assignment_source='auto'`, `discovery_run_id=run_id`. Assignments without a subtopic skip the `video_subtopics` insert (graceful). Subtopic referencing an unknown parent topic, or an assignment-level subtopic not present in `payload.subtopics`, raises `ValueError`. Issue 03 / Ralph iteration 1.)*
- [x] CLI: `analyze --db-path --project-name --channel-input --stub` chains channel resolve → metadata upsert → videos fetch+upsert → `run_discovery(stub_llm)` (Ralph iteration 3, commit `cc70ccd`)
- [x] Slice 04 schema + prompt: `_DISCOVERY_SCHEMA` requires `confidence` (number 0.0–1.0) and `reason` (string) on each assignment item; system prompt asks the model for both per assignment; `DISCOVERY_PROMPT_VERSION` bumped to `discovery-v3`. Schema stays `additionalProperties: false` everywhere. (Issue 04 / Ralph iteration 1 — `confidence` declares `minimum: 0`/`maximum: 1`, `reason` declares `minLength: 1`; current validator type-checks but does not yet enforce numeric range or minLength — slice 04 payload threading will add the runtime guard or extend the validator. Existing fixture-style assignments in `test_discovery.py` (3 `runner.add_response` sites + 2 inline schema tests) updated to include `confidence`/`reason`. The extra-key rejection test still raises — for "missing required `reason`" rather than "extra `confidence`" — re-pointed in the next sub-plan.)
- [x] Slice 04 payload threading: `_payload_from_response` reads `confidence` and `reason` from each assignment item (no longer hardcoded to `1.0` / `""`); `stub_llm` keeps `confidence=1.0` but ships a non-trivial `reason`; the existing extra-key schema test (formerly `confidence`) is re-pointed to a still-unrecognized assignment key. (Issue 04 / Ralph iteration 2 — `discovery._payload_from_response` now reads `item["confidence"]`/`item["reason"]` (float-coerced); `stub_llm` was already shipping `reason="stub assignment"` from slice 03 so unchanged. `test_schema_rejects_assignment_with_extra_keys` now passes valid `confidence` + `reason` plus an extra `priority` key. `test_callable_round_trips_payload_via_extractor` updated to expect the threaded `reason="fixture"` instead of the prior placeholder.)
- [x] Slice 05 prompt + multi-topic stub fixture: relax the discovery prompt's "Every supplied episode must appear exactly once in `assignments`" rule so an episode may have multiple assignment entries (different `topic` values) when it genuinely covers multiple topics; bump `DISCOVERY_PROMPT_VERSION` to `discovery-v4`; extend `stub_llm` so at least one stub video carries two assignments (different topics) so the GUI can verify multi-topic display without paying for an LLM. Schema/persistence already permit multi-row video_topics — no DB change required. (Issue 05 / Ralph iteration 1 — `_DISCOVERY_SYSTEM` rule replaced with "must appear at least once" + explicit anti-over-tagging clause; example JSON now shows one `<id1>` with two `assignments` entries (different topics); `DISCOVERY_PROMPT_VERSION` bumped to `discovery-v4`. New const `STUB_SECONDARY_TOPIC_NAME = "Cross-cutting"`; `stub_llm` now emits N primary-topic rows + 1 secondary-topic row on `videos[0]` (confidence=0.6, no subtopic). Existing `len(...assignments) == 2` count assertions in `test_discover_stub_creates_run_and_assignments`, `test_analyze_chains_setup_ingest_and_discover`, `test_llm_error_does_not_corrupt_prior_successful_run` re-pointed to 3. `test_stub_llm_emits_one_subtopic_per_topic` now filters subtopic-presence to primary-topic assignments. `test_stub_llm_returns_one_topic_covering_all_videos` renamed to `test_stub_llm_assigns_every_video_to_primary_and_one_to_secondary`.)
- [x] Slice 08 sticky curation — rename event log + replay: add `topic_renames(id, project_id, topic_id, old_name, new_name, created_at)` table to `db.py` schema; `/api/discovery/topic/rename` inserts a row after a successful rename. Add `_apply_renames_to_payload(connection, project_id, payload)` in `discovery.py` that rewrites `payload.topics`, `payload.subtopics[i].parent_topic`, and `payload.assignments[i].topic_name` through the project's rename map *before* the topics/subtopics/video_topics inserts run; multi-hop chains collapse to the latest target (A→B then B→C should rewrite incoming "A" straight to "C"). Add `_suppress_wrong_assignments_in_run(connection, channel_id, run_id)` that, after assignment inserts, deletes any `video_topics`/`video_subtopics` rows in this run matching `(video_id, topic_id_by_name)` from `wrong_assignments` for videos in this channel. Wire both into `run_discovery` (rewrite before persistence; suppression after). Tests: rename A→B, second stub run (LLM still emits "A") leaves topic as "B" with all original episodes intact + no orphan "A" topic; `mark-wrong` then second stub run leaves the suppressed assignment absent from `video_topics`. (Issue 08 / Ralph iteration 1 — `topic_renames` table added to `TABLE_STATEMENTS` + `REQUIRED_TABLE_COLUMNS`. `db.rename_topic` instrumented to insert a `topic_renames` row in the same connection/transaction as the `UPDATE topics`. `discovery._apply_renames_to_payload` builds a fixed-point map (with `seen` set guard against cycles) collapsing multi-hop chains, returning a new `DiscoveryPayload` with rewritten `topics` (deduped, first-seen order preserved), `subtopics[i].parent_topic`, and `assignments[i].topic_name`. `_suppress_wrong_assignments_in_run` runs after the final assignment loop, deletes from `video_topics`/`video_subtopics` scoped to the current run + channel. Both wired into `run_discovery`. 5 new tests in `StickyCurationRenameReplayTests`: rename-then-rerun keeps curated name with episodes; mark-wrong-then-rerun suppresses the assignment in the new run; multi-hop A→B→C collapse; payload dedupe after rewrite; rename API records a `topic_renames` row.)
- [x] Slice 08 sticky curation — surface new topics introduced by re-runs: add `_topics_introduced_in_run(connection, channel_id, run_id) -> list[str]` helper computing topic names whose first `video_topics.discovery_run_id` for this channel is the current `run_id` (i.e., never assigned in any prior run on the channel). Extend `_build_discovery_topic_map` payload with `new_topic_names: [<name>, ...]` (empty list when this is the channel's first run, or when no new topics surfaced). JS shows a small "New" badge on topic cards whose name is in `new_topic_names`. Test: payload-shape — second stub run that introduces a topic absent from the first run flags exactly that name in `new_topic_names`; topics also seen in run 1 do not appear; first-ever run reports `new_topic_names: []` (otherwise every topic is "new" on first discovery and the badge becomes meaningless). (Issue 08 / Ralph iteration 2 — `topics` schema gains `first_discovery_run_id INTEGER` (nullable FK to `discovery_runs(id) ON DELETE SET NULL`) recorded on first INSERT and preserved across `ON CONFLICT DO UPDATE` so the first-seen run survives `run_discovery`'s upsert pattern; `MIN(video_topics.discovery_run_id)` alone is unreliable because the existing `ON CONFLICT(video_id, topic_id) DO UPDATE SET discovery_run_id = excluded.discovery_run_id` overwrites prior runs' ids when the same (video, topic) reappears. `_topics_introduced_in_run` in `review_ui.py` filters topics with `vt.discovery_run_id = run_id AND t.first_discovery_run_id = run_id`, returning `[]` when no earlier `discovery_runs` row exists for the channel. `_build_discovery_topic_map` payload now carries `new_topic_names: [...]` (empty list, never null); `renderDiscoveryTopicMap` reads the list into a Set and appends `<span class="discovery-topic-new-badge">New</span>` next to matching topic-card `<h3>` (uses pill-style CSS mirroring `.discovery-episode-also-in`). `UI_REVISION` bumped. 4 new tests in `StickyCurationRenameReplayTests`: introduced-only-new-names; introduced-empty-on-first-run; state-payload-carries-new-topic-names; html-renders-new-topic-badge.)

#### A3. Topic map UI (extend `review_ui.py`)
- [x] `/api/state` payload: `discovery_topic_map` key surfaces latest run's topics with episode count + average confidence (Ralph iteration 4, commit `89437b7`)
- [x] Render auto-discovered topic map in HTML/JS: topics with episode counts, subtopic counts, average confidence (Ralph iteration 5 — panel above the pre-pivot Topic Map; subtopic counts deferred until §A2 LLM produces real subtopics)
- [x] Topic detail: subtopics + episodes assigned to each *(episodes done Ralph iteration 6; subtopic drill-down done issue 03 / Ralph iteration 2 — `_build_discovery_topic_map` now adds per-topic `subtopics: [{name, episode_count, episodes}]` plus `unassigned_within_topic`; `renderDiscoverySubtopicBuckets` renders collapsible `<details>` per subtopic + an "Unassigned within topic" bucket; topic-card stats gain a Subtopics tile.)*
- [x] Per-episode card: title, thumbnail, "why this episode is here" reason, confidence indicator (Ralph iteration 6, commit `f2db466`; faded/muted styling for low confidence; guest deferred — not currently extracted)
- [x] Episodes appear under every topic they belong to (multi-topic display) (Ralph iteration 6)
- [x] Curation actions: rename topic, merge two topics, split a topic, move episode between subtopics, mark assignment as wrong *(rename happy path done Ralph iteration 8 — `/api/discovery/topic/rename`; merge done Ralph iteration 9 — `/api/discovery/topic/merge` re-points video_topics + subtopics with target-wins collision handling; split done Ralph iteration 10 — `/api/discovery/topic/split` creates a new topic, re-points selected video_topics, drops orphan video_subtopics under source for moved videos; move-episode-between-subtopics done Ralph iteration 11 — `/api/discovery/episode/move-subtopic` re-points the video's `video_subtopics` row within the same topic, inserts when no row exists, no-op on target match; mark-wrong done Ralph iteration 12 — `/api/discovery/episode/mark-wrong` deletes the `video_topics` row (also clears any `video_subtopics` rows under that topic) or the specific `video_subtopics` row, recording an event in a new `wrong_assignments` table for slice 08 to consume; curation-survives-rerun deferred to slice 08)*
- [x] Sort options for episode lists: recency, confidence (Ralph iteration 7, JS-side per-topic dropdown, default recency; view count deferred — not currently ingested)
- [x] Configurable low-confidence threshold for episode card styling (env var `YTA_LOW_CONFIDENCE_THRESHOLD`; default 0.5; replaces the hardcoded 0.33/0.66 dual thresholds shipped in iteration 6 — Ralph iteration 13, threshold flows through `_build_discovery_topic_map` payload to JS)
- [x] Test asserting low-confidence episode cards render with the distinct faded/muted style on a mixed-confidence fixture *(Ralph iteration 13 — `DiscoveryLowConfidenceThresholdTests` seeds a 0.2/0.5/0.9 fixture and asserts `_low_confidence_class` marks the sub-threshold one as `low`; HTML test confirms `.discovery-episode.low` CSS still ships the faded/muted style)*
- [x] Document the sort-persistence decision (per-topic dropdown resets to recency on reload; not persisted) in the issue 09 spec — Decisions section (Ralph iteration 14)
- [x] Slice 05 multi-topic episode card: when an episode is assigned to ≥2 topics in the current discovery run, each card surfaces an inline "also in: <other topics>" pill listing the *other* topics it appears under. `_build_discovery_topic_map` payload extends each per-topic episode dict with an `also_in: [<topic_name>, ...]` list; the JS card renderer shows the pill only when non-empty. (Issue 05 / Ralph iteration 2 — `_build_discovery_topic_map` now precomputes `topics_by_video` from the same `episode_rows` query (no extra SQL) and stamps each per-topic episode dict with `also_in` filtered to exclude the current topic; defaults to `[]` (never null) so the JS check stays simple. `renderDiscoveryEpisodeItem` gains a 4th arg `showAlsoIn` (default falsy); only the top-level topic-list call site (line ~1150) passes `true`, so subtopic drill-down + unassigned-within-topic buckets do not show the pill, per overlay scope. New `.discovery-episode-also-in` CSS lives next to `.discovery-episode-meta` (existing inline-meta pattern, no new class hierarchy). Two tests added: payload-shape `test_state_payload_episode_dicts_carry_also_in_for_multi_topic` (vid1 in Health+Business → each card sees the other; vid2 only in Business → empty list) and HTML wiring `test_html_page_renders_also_in_pill_for_multi_topic_episodes`.)

#### A4. Move legacy code
- [x] Create `legacy/` directory (package with empty `__init__.py`)
- [x] Move `comparison_group_suggestions.py`, `group_analysis.py` to `legacy/`
- [x] Move group-related parts of `markdown_export.py` to `legacy/` (whole file — every symbol is group-export code)
- [x] Move full-transcript pipeline parts of `processing.py` to `legacy/` (whole file — `db.py` and `cli.py` import from `legacy.processing`)
- [x] Remove comparison-group surfaces from the GUI primary navigation (page-header button + per-subtopic action button dropped; API routes + helpers + state payload kept intact for any external callers)
- [x] Update `cli.py` so comparison-group commands still work but warn that they're legacy (`_warn_legacy()` stderr line on entry to all 21 group/comparison-group commands)

#### A5. Documentation and operator guidance
- [x] Document the Phase A end-to-end operator workflow
- [x] Update `YT_ANALYZER_CHEATSHEET.md` to reflect the new primary commands
- [x] First real run: ingest Diary of a CEO, run discovery, review the resulting topic map *(also subsumes issue 03 criterion 6: validate ≥2 subtopics per topic on a real channel — 2.17 avg; also subsumes issue 04 criterion 5: validate model-emitted confidence + reason quality on a real channel — spread 0.85–0.95, reasons grounded in titles; evidence: `.scratch/issue-10/doac-smoke-20260507-221241.log`)*

#### A6. GUI plan finish — Channel Overview

Implements the **Channel Overview** section from `GUI_UX_PLAN.md` (the only fully-missing section of the five top-level sections in that plan; Priority 2 run-ID demote and Priority 4 transcript-aware comparison readiness will land in subsequent slices).

- [x] Add `_build_channel_overview(db_path, project_id, channel_id) -> dict` helper to `review_ui.py` and surface it under a new `channel_overview` key on the `/api/state` payload. Counts to surface: channel title + id, total video count, transcript count (from `video_transcripts`), distinct topic count (topics with at least one `video_topics` row in this channel), distinct subtopic count (same shape via `video_subtopics`), comparison group count, latest `discovery_runs` row for this channel (id, status, started_at, model, prompt_version, plus a flag for whether any has ever run). Use a single connection / minimal queries — extend the existing connection pattern; no new module. Persistence-shape test in `test_discovery.py` or a new `test_review_ui.py` asserts every key is present and counts match a seeded stub-discovery fixture. (Issue 11 / Ralph iteration 1 — `_build_channel_overview` placed before `_topics_introduced_in_run`; uses one `sqlite3.connect` and seven small reads (channels row, videos count, transcripts count via JOIN videos, distinct topic_id via `video_topics` JOIN videos, distinct subtopic_id via `video_subtopics` JOIN videos, distinct comparison_group_id via `comparison_group_videos` JOIN videos, latest `discovery_runs` row scoped by channel_id). All counts scope through `videos.channel_id` since `video_topics`, `video_subtopics`, `comparison_group_videos`, and `video_transcripts` all FK to `videos.id`. `latest_discovery` is `None` when the channel has no runs; otherwise an object with `id`/`status`/`started_at` (aliased from `discovery_runs.created_at`)/`model`/`prompt_version`. `build_state_payload` now adds the `channel_overview` key alongside `discovery_topic_map`. Two tests in new `ChannelOverviewPayloadTests`: seeded stub-discovery shape (counts: 2 videos / 0 transcripts / 2 topics / 0 subtopics / 0 comparison groups, plus latest_discovery fields) and empty-DB shape (latest_discovery is `None`, counts zero except `video_count=2`). project_id parameter is plumbed through but unused by the current scoping (channel_id alone suffices); kept to match the spec'd signature for the polish iteration.)
- [x] Render the Channel Overview panel as a new top-of-page section in `review_ui.py` HTML (above the existing Discovery Topic Map). Panel header shows channel title + id; body is a row of stat tiles (Videos / Transcripts / Topics / Subtopics / Comparison groups) plus a "Latest discovery" block showing run id + status + started_at + model + prompt_version. Empty state: when no `discovery_runs` row exists, the latest-discovery block shows "No discovery yet — run `analyze` or `discover` to start." HTML wiring test asserts the panel renders with the seeded fixture values and that the empty-state copy appears when no discovery has run. (Issue 11 / Ralph iteration 2 — new `<section class="panel channel-overview">` inserted between the topbar and the Discovery Topic Map section; header shows channel title + "Channel ID: <id>" subtitle. Stat tiles reuse the existing `.topic-stat` style inside a new auto-fit `.channel-overview-stats` grid (5 tiles: Videos / Transcripts / Topics / Subtopics / Comparison groups). `renderChannelOverview(payload.channel_overview)` wired into `render()` immediately after `renderContext` and before `renderDiscoveryTopicMap`. Empty-state copy lives in the JS branch when `latest_discovery` is null: "Latest discovery · No discovery yet — run `analyze` or `discover` to start." `UI_REVISION` bumped to `2026-05-08.2-channel-overview-above-discovery-panel` (kept the "discovery" substring so existing `test_ui_revision_advances_for_*` assertions still pass). 6 new tests in `ChannelOverviewHTMLTests`: panel markup IDs present; panel ordered above discovery topic map; render function defined + wired into `render()`; all 5 stat-tile labels appear in the JS source; empty-state copy mentions both `analyze` and `discover` commands; `UI_REVISION` contains `channel-overview`. Polish iteration handles the no-primary-channel + empty-DB no-crash paths.)
- [x] Loose-end polish + COMPLETE: ensure the panel doesn't double-count or break when `primary_channel` is unset (graceful "no primary channel" state); confirm `UI_REVISION` is bumped if the JS shape changed; ensure empty-DB integration path (no videos, no runs, no topics) renders without errors. Final iteration emits COMPLETE. (Issue 11 / Ralph iteration 3 — `build_state_payload` now wraps `get_primary_channel(db_path)` in `try/except ValueError` (the function raises rather than returns `None`); when no primary channel exists, `channel_overview`, `channel_title`, and `channel_id` are set to `None` instead of crashing the whole `/api/state`. `renderChannelOverview` empty-overview branch now sets the subtitle to "No primary channel set" so the panel renders a meaningful header rather than a blank one. `UI_REVISION` bumped to `2026-05-08.3-channel-overview-no-primary-channel-discovery-panel` (keeps `channel-overview` and `discovery` substrings so all 9 prior `test_ui_revision_advances_for_*` tests stay green). 2 new tests: `test_state_payload_channel_overview_null_when_no_primary_channel` (empty DB doesn't raise; `channel_overview`/`channel_title`/`channel_id` all `None`) and `test_html_page_renders_no_primary_channel_hint` (JS source carries the hint string). Empty-DB-with-channel safety was already covered by `test_state_payload_channel_overview_latest_discovery_null_when_no_run` from iteration 1.)

#### A7. GUI plan finish — Run-ID demote (Priority 2)

Implements **Priority 2** of `GUI_UX_PLAN.md` ("Replace run-ID-first navigation"). The legacy suggestion-run dropdown sits in the topbar's primary controls today (`review_ui.py:781`); routine work should not require picking a run ID. Move it into a collapsed Advanced / Run history block, and make the subtopic review default to the latest run that has labels for the currently selected parent topic (rather than just the latest run overall — the latest run may not have subtopics for that topic).

- [x] Relocate the `<select id="run-select">` markup out of the topbar primary `.controls.row` into a new collapsed `<details class="run-history-advanced">` block placed below the topbar `.generator` (so it stays available for auditability without occupying primary nav). Summary reads "Run history (advanced)"; the inner block keeps the existing select plus a one-line muted hint ("Pick an older run to inspect its labels. Routine review uses the latest run automatically."). Topic-select and subtopic-select stay in the primary `.controls.row`. Bump `UI_REVISION` (preserve the `channel-overview` and `discovery` substrings so prior `test_ui_revision_advances_for_*` assertions hold). HTML wiring test: rendered page contains `<details class="run-history-advanced">` wrapping `id="run-select"`, and the select markup no longer appears inside the primary `.controls.row` block. (Issue 12 / Ralph iteration 1 — moved the run-select `<label>` block out of the topbar primary `.controls.row` (lines 778–792) into a new `<details class="run-history-advanced">` block placed between the `.generator` div and the `status-box` div, still inside `<section class="topbar">`. Inner markup: `<summary>Run history (advanced)</summary>`, a `.run-history-hint` muted line with the spec'd copy, then the existing `<label>Suggestion run / <select id="run-select"></select></label>` unchanged. Topic-select + subtopic-select stay in `.controls.row`. Minimal CSS added: `.run-history-advanced` (margin/padding/border-top mirror `.generator`), `> summary` (cursor pointer + muted color), `.run-history-hint` (margin-top), `> label` (max-width: 320px so the select doesn't stretch the panel). `UI_REVISION` bumped to `2026-05-08.4-run-history-advanced-channel-overview-discovery-panel` — keeps both `channel-overview` and `discovery` substrings so all 10 prior `test_ui_revision_advances_for_*` assertions stay green. 5 new tests in new `RunHistoryAdvancedHTMLTests`: details wraps run-select + summary copy; run-select absent from primary `.controls.row`; topic/subtopic selects still in primary `.controls.row`; hint copy ships in HTML; `UI_REVISION` carries all three substrings.)
- [x] When the user changes the parent-topic selector for subtopic review, default the active run to the *latest run that has subtopic labels for that topic* (rather than the latest overall run). Add `_latest_subtopic_run_id_for_topic(db_path, topic_name) -> int | None` helper in `review_ui.py`; extend `build_state_payload` so the response carries `latest_subtopic_run_id_by_topic: {<topic_name>: <run_id>, ...}` covering every topic in `topic_reviews` (empty dict when no topic-suggestion runs exist). JS topic-select change handler reads the map and, if a run id is found for the chosen topic, sets `run-select.value` to that id before the next `fetchState`. Test: payload-shape — given two runs where run #1 has subtopics for topic A but run #2 only for topic B, `latest_subtopic_run_id_by_topic["A"] == 1` and `["B"] == 2`. (Issue 12 / Ralph iteration 2 — added `_latest_subtopic_run_id_for_topic(db_path, topic_name)` (single-row `MAX(suggestion_run_id)` over `subtopic_suggestion_labels JOIN topics` filtered by `topics.name`) plus a bulk `_latest_subtopic_run_ids_by_topic(db_path)` (one `GROUP BY topics.name` query) used by `build_state_payload` to populate the new `latest_subtopic_run_id_by_topic: dict[str, int]` payload key. Bulk query keeps payload assembly O(1) DB hits regardless of topic count; the standalone helper is the spec-mandated public surface for ad-hoc lookups. JS topic-select listener now reads `state.payload.latest_subtopic_run_id_by_topic[newTopic]` and, when present and different from the current `run-select.value`, snaps `run-select.value` to that run id *before* `fetchState({ topic, subtopic: null })` runs — so the subsequent `selectedRunId()` call in `fetchState` picks up the new run id. 5 new tests in `LatestSubtopicRunIdByTopicTests`: helper returns max run id per topic given two runs with subtopic labels split across them; helper returns `None` for topic with no subtopic-suggestion rows and for unknown topic name; payload carries the dict matching the per-topic max run ids; payload dict is `{}` on a fresh DB with no subtopic-suggestion runs; HTML wiring asserts the JS topic-select change handler block references both `latest_subtopic_run_id_by_topic` and `run-select`.)

---

## Future phases (planned, not active)

### Phase B — Sample-based taxonomy refinement
Priority: medium

Resume after Phase A has been used on at least one real channel for a week.

- [ ] Add transcript fetching for a sampled subset (15–20 episodes representative of all topics)
- [ ] Run a coarse claim extraction pass on the sample
- [ ] Cluster sampled claims; surface clusters that don't fit the existing taxonomy
- [ ] User reviews proposed taxonomy additions/splits
- [ ] Apply changes to the topic map

### Phase C — Full claim extraction and synthesis
Priority: medium-high (once Phase A feels right)

- [ ] Full transcript ingestion for the channel (`youtube-transcript-api`; Whisper fallback if needed)
- [ ] Boilerplate filtering (ad reads, intros)
- [ ] Claim extraction prompt (Haiku batch): atomic claims with topic, subtopic, speaker, claim type, confidence signals, source episode + timestamp
- [ ] `claims` table with full provenance
- [ ] Embed claims via local sentence-transformers; store in `sqlite-vec` table
- [ ] Per-topic clustering of claims
- [ ] Consensus surfacing (clusters with many distinct guests)
- [ ] Conflict surfacing (contradictory clusters within a topic — needs an LLM compare step)
- [ ] "Most useful advice" surfacing (advice-typed claims, ranked by cluster density and guest diversity)
- [ ] UI: per-topic Synthesis tab with consensus / conflict / advice sections
- [ ] All claim views link back to source episode + timestamp

### Phase D — Natural-language Q&A
Priority: medium-later

- [ ] Query embedding + retrieval over claim store
- [ ] Synthesis call (Sonnet) with retrieved claims as context
- [ ] Answers always cite source episodes + timestamps
- [ ] UI: search bar / question box on the topic map; dedicated answer view with sources

---

## Intentionally deferred or out of scope

Do not jump these unless priorities explicitly change:

- Multi-channel support (cross-channel querying, comparison)
- Public deployment, multi-user, auth
- Docker polish
- A separate vector DB (we're using `sqlite-vec`)
- Auto-applying AI suggestions without human review
- Continuous re-ingestion / live monitoring of new episodes (batch is fine for now)
- Mobile / native UI

---

## Open questions to revisit

- Does Phase A's metadata-only discovery produce a topic map that feels right for DOAC? If not, do we need chapter markers more aggressively, or jump to Phase B sooner?
- How visible should confidence be in the UI — a number, a faded style, both?
- After Phase A is in use, is the GUI still the primary surface, or do power users live in the CLI?
- For Phase C, is per-claim provenance (episode + timestamp) enough, or do we need to store the surrounding transcript window?
- For Phase D, what's the right answer length and citation format?

---

## Resume priorities

If resuming after a gap, start here:

1. Read `PROJECT_SUMMARY.md`, `CURRENT_STATE.md`, `PRD_PHASE_A_TOPIC_MAP.md`.
2. Check the latest `WORKLOG.md` entries for what was last worked on.
3. Find the next unchecked item in **Phase A — concrete next steps** above.
4. Verify the relevant workflow before changing it.
5. Make the smallest useful change.
6. Update `CURRENT_STATE.md` if the situation has shifted.
7. Update this roadmap if priorities have shifted.
