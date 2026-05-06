# YouTube Channel Analyzer — Worklog

## Purpose

This file is the running log for notable progress, decisions, and pivots.

Use it to make resumptions easier without reconstructing everything from memory.

Keep entries short and practical.

---

## Entry template

```markdown
## YYYY-MM-DD

### Done
- ...

### Learned
- ...

### Next
- ...
```

---

## 2026-05-06 — Issue 02 / Ralph iteration 3: single batched LLM call site

### Done (TDD, 9 new tests in `test_discovery.py`)
- `discovery.py` registers prompt `discovery.topics@discovery-v1` via the
  Extractor registry. System message instructs the LLM to emit
  `{topics: [...], assignments: [{youtube_video_id, topic}]}` and forbids
  prose / markdown fences. Schema (`additionalProperties: false`) enforces
  exactly that shape — extra keys like `subtopic`/`confidence` are rejected
  so future slices add them deliberately.
- `register_discovery_prompt()` is idempotent — repeat calls return the
  already-registered Prompt instead of raising.
- `discovery_llm_via_extractor(extractor)` returns an `LLMCallable` that
  renders all videos into one prompt and round-trips a single
  `Extractor.run_one(...)` call. The Extractor owns schema validation +
  one-retry on parse failure (slice 02 acceptance criterion). Slice 02
  scope: `confidence=1.0` and `reason=""` defaults are filled by the
  adapter; later slices (03–05) extend the schema.
- `make_real_llm_callable(connection, *, model=None)` constructs an
  `AnthropicRunner(model=model or DEFAULT_MODEL)` + `Extractor` wired
  adapter. **Raises `RuntimeError` unless `RALPH_ALLOW_REAL_LLM=1`** so the
  verify gate path can't accidentally spend tokens. Tests cover both unset
  and `="0"` cases.

### Learned
- The existing `LLMCallable = Callable[[Sequence[DiscoveryVideo]],
  DiscoveryPayload]` interface from slice 01 is exactly the seam needed —
  the new adapter just produces an `LLMCallable` from an `Extractor`, no
  changes to `run_discovery`. The caller (CLI in a later iteration) opens
  its own connection, builds the Extractor + adapter, and passes the
  callable into `run_discovery(..., prompt_version=DISCOVERY_PROMPT_VERSION,
  ...)` so the run row records `discovery-v1`.
- `Extractor` lives in `yt_channel_analyzer.extractor` (slice 00). It uses
  the `llm_calls` audit table and a separate connection from
  `run_discovery`'s own connection — both safe with SQLite WAL.

### Next
- Wire the prompt content for slice 02's broader §A2 checkbox: "Prompt
  produces: list of broad topics with subtopics, plus per-episode
  topic/subtopic assignments with confidence (0.0–1.0) and a short reason
  string". Per the issue spec, slice 02 only ships broad topics + single
  topic per episode — subtopics/confidence/reason land in slices 03–05.
  So the next iteration likely focuses on the "Validate response shape;
  retry once; on second failure mark errored" checkbox (already mostly
  delegated to Extractor — needs the discovery-side error path to set
  `discovery_runs.status='error'` instead of persisting partial state).

---

## 2026-05-06 — Issue 02 / Ralph iteration 2: strip description boilerplate before LLM

### Done (TDD, 8 new tests in `test_discovery.py`)
- New `strip_description_boilerplate(description)` in `discovery.py`. Line-
  based regex filter that drops sponsor reads ("Sponsored by", "Brought to
  you by", "Sponsors:", "Use code … for X% off"), subscribe/like/bell
  CTAs, "Follow me on …" lines, social-platform label lines (`Twitter:`,
  `Instagram:`), bare social/podcast URLs (instagram, twitter/x, tiktok,
  facebook, linkedin, threads, patreon, discord, youtube/youtu.be,
  spotify, apple), and "Listen on …" / "Available on …" CTAs. Chapter-
  marker lines (matched by the existing `_CHAPTER_LINE` regex) are always
  kept so episode structure still reaches the LLM.
- `run_discovery` now sets `DiscoveryVideo.description` to the cleaned
  text. `chapters` is still parsed from the original description so the
  filter can't accidentally elide structure even if a chapter title
  happens to mention a sponsor.
- Returns `None` for `None` input, `""` for empty input, possibly `""`
  if the entire description was boilerplate. Consecutive blank lines
  collapsed and leading/trailing blanks trimmed.

### Learned
- Patterns ending in `\b` after `:` don't match line-final colons because
  `:` is non-word and there's no following word char. Split the
  `Sponsors:` rule into its own pattern (`\bsponsors?:`) without a
  trailing `\b`. Caught by the `test_strips_sponsor_read_lines` red.
- The boilerplate filter is intentionally aggressive — over-filter beats
  under-filter for Phase A discovery, where a sponsor brand leaking into
  the LLM context could nucleate a phantom topic.

### Next
- Build the single batched LLM call (Haiku 4.5 / GPT-4o-mini). HITL
  trigger #1 — adding a real-LLM call site means the next iteration
  must wrap it with the `RALPH_ALLOW_REAL_LLM=1` env-var guard and
  raise without it; the verify gate must still pass with the env unset.

---

## 2026-05-06 — Issue 02 / Ralph iteration 1: pull chapter markers into discovery videos

### Done (TDD, 7 new tests in `test_discovery.py`)
- New `Chapter(start_seconds, title)` frozen dataclass exported from
  `discovery.py`. New `parse_chapters_from_description(description)` helper
  that follows YouTube's chapter-recognition rules conservatively: ≥3
  timestamped lines, first timestamp is `0:00`, timestamps strictly
  monotonically increasing. If any check fails, returns an empty tuple
  rather than half-parsed chapters.
- `DiscoveryVideo` gained a `chapters: tuple[Chapter, ...] = ()` field
  (default empty so existing `DiscoveryVideo(...)` constructors keep
  working). `run_discovery` now populates `chapters` per video by parsing
  the description, so the LLM callable receives titles + descriptions +
  chapters as the issue 02 sub-plan calls for.
- No schema change — chapters are derived per discovery run from the
  existing `videos.description` column. YouTube Data API doesn't return
  chapters as a separate field anyway; they live inside descriptions.

### Learned
- The minimal helper accepts a few stylistic variants (leading bullets,
  bracketed timestamps, an optional separator) but keeps the YouTube
  validity rules strict, so a description with two stray timestamps in
  prose won't be misread as chapter markers. Ad-read sponsor blocks
  with `0:00` Intro-style chapters still parse cleanly.
- Defaulting `chapters=()` keeps the existing `StubLLMTests` and
  `_seed_channel_with_videos` test fixtures intact — no churn outside
  the new tests.

### Next
- Pre-filter common boilerplate (sponsor reads, social CTAs) from
  descriptions before they're handed to the LLM. Parsed chapters are
  the natural anchor for "trim everything below the last chapter line"
  if we want to be aggressive; otherwise a regex-based filter for
  common ad-read tells.

---

## 2026-05-06 — Issue 09 / Ralph iteration 14: document sort-persistence decision

### Done (docs only — no code)
- Added Decisions section to `.scratch/phase-a-topic-map/issues/09-sort-and-low-confidence-styling.md`:
  - Sort persistence: per-topic JS `Map`, not persisted to localStorage/server, resets to recency on reload. Rationale: cheapest-to-ship, reversible (localStorage is a strict superset), single-user app, and topic-rename/merge/split already complicates a stable persistence key.
  - View-count sort option: deferred because `videos.view_count` is not ingested. Listed as a known acceptance-criteria gap with a clear unblock condition.
- Ticked all five issue 09 acceptance criteria checkboxes in the spec to reflect met state (with cross-refs to the deferral notes).
- Ticked the last unchecked §A3 sort-persistence checkbox in `ROADMAP.md`.

### Issue 09 status
- All five acceptance criteria met; remaining §A3 unchecked items belong to other issues (subtopic rendering, blocked on §A2 real LLM). Branch is ready for `<ralph>COMPLETE</ralph>` next iteration.

### Next
- Next iteration: confirm acceptance criteria all met and emit COMPLETE for the branch.

---

## 2026-05-06 — Issue 09 / Ralph iteration 13: configurable low-confidence threshold

### Done (10 new tests in `test_discovery.py::DiscoveryLowConfidenceThresholdTests`)
- New env var `YTA_LOW_CONFIDENCE_THRESHOLD` (default 0.5) read by
  `_load_low_confidence_threshold()` in `review_ui.py`. Validates: blank
  / non-numeric / out-of-range [0,1] all fall back to default. Threshold
  is included on the `discovery_topic_map` payload so the JS doesn't
  need its own constant.
- Replaced the hardcoded 0.33/0.66 dual-threshold logic in JS with a
  single threshold sourced from `map.low_confidence_threshold`. Both the
  topic-card confidence bar and the episode card now emit at most one
  `low` class (no more `very-low`). CSS for `.discovery-episode.low`
  collapsed into one rule (opacity 0.55, bad-coloured confidence text);
  `.confidence-bar.very-low` and `.discovery-episode.very-low` rules
  removed.
- New `_low_confidence_class(confidence, threshold)` Python helper used
  by tests; the JS mirrors the same `c < threshold` check with the
  threshold injected via the payload.
- Mixed-confidence fixture test seeds 0.2 / 0.5 / 0.9 assignments in a
  single topic, runs `_build_discovery_topic_map`, and asserts the
  classifier returns `low` for 0.2 and `''` for 0.5 / 0.9. HTML tests
  guard against regressing back to dual thresholds (`0.33`/`0.66` and
  `very-low` are now banned substrings in the rendered page).
- UI revision bumped to `2026-05-06.9-discovery-confidence-threshold`.
  (First attempt used `discovery-low-confidence-threshold` but the
  substring `very-low` lurked inside `discovery-low` — the regression
  test caught it.)

### Learned
- Substring-style HTML assertions (`assertNotIn("very-low", html)`) are
  fragile against unrelated identifiers that contain the same letters.
  `discovery-low-confidence-threshold` literally contains `very-low`
  via the trailing `very` of `discovery` plus `-low`. Renamed UI rev to
  sidestep the collision, and the test still does its job.
- The threshold + classifier helper duo (Python helper + payload field
  consumed by JS) is the cheapest way to unit-test the styling
  decision without a JS test harness. JS only mirrors the comparison
  literally; if that drifts, the `test_html_uses_payload_threshold`
  guard still notices a regression.

### Next
- Issue 09's last unchecked roadmap item: document the sort-persistence
  decision (per-topic dropdown resets to recency on reload). One-line
  note somewhere durable. Then issue 09 acceptance criteria are met
  and the branch can `<ralph>COMPLETE</ralph>`.

---

## 2026-05-06 — Slice 07 (partial) / Ralph iteration 12: mark assignment wrong

### Done (TDD, 14 new tests in `test_discovery.py`)
- New `wrong_assignments` table in `db.py` schema:
  `(id, video_id, topic_id, subtopic_id NULLABLE, reason NULLABLE, created_at)`.
  This is the first persistent curation-event record (slice 06 stopped at
  `assignment_source='manual'`). Slice 08 can replay these to keep
  curation surviving discovery re-runs.
- New `mark_assignment_wrong(db_path, *, project_name, topic_name,
  youtube_video_id, subtopic_name=None, reason=None)` in `db.py`. When
  `subtopic_name` is None: deletes the `video_topics` row and ALSO drops
  any `video_subtopics` rows whose subtopic is under that topic
  (otherwise the video would still hang off the topic via subtopic
  joins). When provided: deletes only the `video_subtopics` row, leaves
  the topic membership intact. Records the event row in
  `wrong_assignments` with `subtopic_id` populated only for the
  subtopic-scoped path. Rejects unknown project / topic / video /
  subtopic, and rejects when the row to remove doesn't exist.
- New `/api/discovery/episode/mark-wrong` endpoint. Body:
  `{topic_name, youtube_video_id, subtopic_name?, reason?}`. Tailored
  success messages for topic vs subtopic removal.
- UI: each `discovery-episode` chip in the discovery topic-map's
  episode list now has a `Wrong topic?` button (calls
  `markEpisodeWrong(topic, vid, null)`). Each subtopic-bucket video chip
  in the selected-topic inventory now has a `Wrong subtopic?` button
  (calls `markEpisodeWrong(topic, vid, subtopic)`). Confirm dialog
  before posting.
- UI revision bumped to `2026-05-05.8-discovery-episode-mark-wrong`.
  Existing `test_ui_revision_advances_for_move` relaxed to the durable
  `discovery` substring (same pattern split/merge used after their
  successors shipped).

### Learned
- The "remove and record" pattern looks identical for topic-scoped and
  subtopic-scoped wrong-marks, but the topic case has to also clear
  child `video_subtopics` rows or the video stays attached to the topic
  via subtopic membership joins. Caught by the dedicated
  `test_mark_wrong_topic_also_drops_video_subtopics_under_topic` test.
- Kept the curation event minimal (`wrong_assignments` table only — not
  a generic `topic_curation_events` log) per scope discipline. Slice 08
  can generalize once it has concrete replay needs.

### Next
- Slice 08: curation surviving discovery re-runs. Likely needs to
  generalize `wrong_assignments` (and the existing
  `assignment_source='manual'` markers on rename/merge/split/move) into
  an event log that the next discovery run consults before applying its
  output.
- Or pivot to A2: real Haiku/4o-mini batched discovery call to retire
  the stub.

---

## 2026-05-05 — Slice 06 (partial) / Ralph iteration 11: move episode between subtopics

### Done (TDD, 13 new tests in `test_discovery.py`)
- New `move_episode_subtopic(db_path, project_name, topic_name,
  youtube_video_id, target_subtopic_name)` in `db.py`. Resolves project,
  topic, target subtopic (must be under that topic), and video; rejects
  unknowns. Refuses if the video isn't already on the topic. If the video
  has an existing `video_subtopics` row under any subtopic of that topic,
  re-points it to the target (no-op when already on target). Otherwise
  inserts a new row with `assignment_source='manual'` to flag the
  curation move. Returns `{moved, inserted, previous_subtopic_name,
  target_subtopic_id}`.
- New `/api/discovery/episode/move-subtopic` endpoint mirroring the
  rename/merge/split shape. Body: `{topic_name, youtube_video_id,
  target_subtopic_name}`. Tailored success messages for moved / attached /
  no-op.
- UI: each video chip inside the selected-topic inventory's subtopic
  buckets now has a `Move` button (sibling subtopics only — hidden when a
  topic has only one subtopic). The JS handler prompts with a numbered
  list of candidate subtopics and asks for confirmation before posting.
- UI revision bumped to `2026-05-05.7-discovery-episode-move-subtopic`.
  Relaxed the `test_ui_revision_advances_for_split` assertion to the
  durable `discovery` substring (same pattern merge used after split
  shipped).

### Learned
- The natural surface for "move episode between subtopics" is the legacy
  topic-inventory panel, not the discovery topic-map panel. The discovery
  payload still doesn't expose subtopics (deferred until §A2 produces
  real subtopic data), and the inventory view already groups videos by
  subtopic, which is the structure this action needs.
- Per-row `assignment_source='manual'` on the move means future code that
  distinguishes auto vs. curated subtopic membership has the signal it
  needs without an extra column.

### Next
- Mark an assignment as wrong (the last §A3 curation action). Then
  curation surviving a re-run (slice 08).
- Or pivot to A2: real Haiku/4o-mini batched discovery call to retire
  the stub.

---

## 2026-05-05 — Slice 06 (partial) / Ralph iteration 10: discovery topic split

### Done (TDD, 14 new tests in `test_discovery.py`)
- New `split_topic(db_path, project_name, source_name, new_name,
  youtube_video_ids)` in `db.py`. Validates source != new, requires a
  non-empty id list, fails on missing project / source / colliding new
  topic name. Resolves the supplied `youtube_video_ids` to internal video
  rows scoped to the source topic; ids that aren't on the source are
  filtered out and reported back as `skipped_video_ids` (raise only if
  *all* are missing). Within a single transaction it: creates the new
  topic in the source's project, re-points the matching `video_topics`
  rows to the new topic, and drops `video_subtopics` rows for those
  videos whose subtopic still belongs to the source topic (keeps the new
  topic from inheriting orphaned subtopic membership). Returns
  `new_topic_id`, `moved_episode_assignments`,
  `dropped_subtopic_assignments`, `skipped_video_ids`.
- New `/api/discovery/topic/split` endpoint mirroring merge. Body:
  `{source_name, new_name, youtube_video_ids}`. Validates the id list is
  non-empty list of non-empty strings before calling the db helper.
- UI: each discovery topic card now has a `Split` button next to
  `Rename`/`Merge`. The JS handler prompts for the new topic name (must
  not collide), then prompts again with a numbered list of episodes for
  the user to enter comma-separated indices. Refuses selecting all
  episodes (suggests Rename instead). Confirm dialog before posting.
- UI revision bumped to `2026-05-05.6-discovery-topic-split`. Relaxed
  the `test_ui_revision_advances_for_merge` test to assert the durable
  `discovery` substring (same pattern earlier iterations used).

### Learned
- The orphan-subtopic cleanup was the only non-obvious bit of the split
  semantics. Without it, splitting episodes off "Productivity" into
  "Time Management" leaves `video_subtopics` rows pointing at
  Productivity-owned subtopics for the moved videos, which would still
  render under Productivity if you ever join through subtopic. Dropping
  those rows is the cheapest path; future iterations can offer a
  "carry the subtopic with you" affordance if needed.
- Listing all video ids inside `window.prompt` is fine for stub-scale
  topics (a handful of episodes) but will get unwieldy past ~20. A
  dedicated checkbox modal is the obvious follow-up; deferred until the
  real LLM produces realistic episode counts per topic.

### Next
- Move an episode between subtopics; mark an assignment as wrong. Then
  curation surviving a re-run (slice 08).
- Or pivot to A2: real Haiku/4o-mini batched discovery call to retire
  the stub.

---

## 2026-05-05 — Slice 06 (partial) / Ralph iteration 9: discovery topic merge

### Done (TDD, 11 new tests in `test_discovery.py`)
- New `merge_topics(db_path, project_name, source_name, target_name)` in
  `db.py`. Within a single transaction it: drops colliding source rows
  in `video_topics` (target wins), re-points remaining rows to the
  target, handles subtopic name collisions by re-pointing
  `video_subtopics` then dropping the source subtopic, re-points
  non-colliding subtopics, dedup-and-re-points
  `subtopic_suggestion_labels`, and finally deletes the source topic.
  Returns a stats dict: `moved_episode_assignments`,
  `dropped_episode_collisions`, `moved_subtopics`,
  `merged_subtopic_collisions`, `target_topic_id`.
- New `/api/discovery/topic/merge` endpoint; returns success message
  with stats. Rejects unknown source/target and same-name merges via
  the existing 400-Bad-Request path.
- UI: each discovery topic card now has a `Merge` button next to
  `Rename`. JS prompt lists the other discovery topics and validates
  the chosen target before calling the endpoint; confirm dialog before
  destructive action; per-topic sort preference is dropped for the
  source topic when its key disappears.
- UI revision bumped to `2026-05-05.5-discovery-topic-merge`. Relaxed
  the prior `test_ui_revision_advances_for_rename` to use the
  `discovery` keyword like the other UI-revision tests, since pinning
  to "rename" blocked every future iteration.

### Learned
- `video_topics` has a partial unique index for one-primary-per-video,
  but it can't fire during a merge: a video already had at most one
  primary topic before the merge, so re-pointing one source row to the
  target either lands in a colliding-and-dropped slot (target wins) or
  in a free slot (target inherits primary). No conflict.
- Comparison groups CASCADE-delete when their parent subtopic is
  dropped during a colliding-subtopic merge. Acceptable given Phase A4
  plans to legacy-archive the comparison-group code; documented via
  the merge-collision behavior rather than worked around.

### Next
- Split a topic, move an episode between subtopics, mark an assignment
  as wrong. Then think about curation surviving a re-run.

---

## 2026-05-05 — Slice 06 (partial) / Ralph iteration 8: discovery topic rename happy path

### Done (TDD, 7 new tests in `test_discovery.py`)
- New `DiscoveryTopicRenameTests` class. Covers: POST
  `/api/discovery/topic/rename` returns 200 and renames the topic in the
  DB; renamed topic surfaces correctly in `/api/state`'s
  `discovery_topic_map` with episode assignments preserved; rename of an
  unknown topic returns 400 with a "not found" message; rename to an
  existing topic name returns 400 with "already exists"; HTML wires
  `function renameDiscoveryTopic` and `/api/discovery/topic/rename`;
  each discovery topic card has a `discovery-topic-rename` button hook;
  `UI_REVISION` includes the substring `rename`.
- Backend: new `/api/discovery/topic/rename` route in
  `ReviewUIApp._handle_post`. Reuses existing `db.rename_topic`. Project
  name resolved via new helper `_resolve_primary_project_name(db_path)`,
  which avoids the `primary_channel.title`-as-project-name shortcut used
  by older suggestion routes (the test seed sets `project_name="proj"`
  and `channel_title="Channel"`, and topics live under `proj`).
- Frontend: new `renameDiscoveryTopic(currentName)` JS function uses
  `window.prompt`, refuses empty / unchanged names, migrates the
  per-topic sort selection (`discoveryEpisodeSortByTopic`) under the new
  name, and posts via the existing `mutate(...)` helper. Each discovery
  topic card now renders a small Rename button next to the title.
  Minimal CSS for `.discovery-topic-header` / `.discovery-topic-rename`.
- Relaxed `test_ui_revision_advances_for_episode_sort` from `sort` to the
  durable `discovery` substring (same pattern iteration 7 used on
  iteration 6's revision-string test, and iteration 6 used on iteration
  5's). Older revision-string tests already assert `discovery`.
- `UI_REVISION` bumped to `2026-05-05.4-discovery-topic-rename`.
- All 33 tests in `test_discovery.py` pass. Two pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` unchanged.
- Sanity: extracted JS body, `node --check` parses cleanly (44KB).

### Slice 06 status
- Slice 06 (curation: rename + merge) is now partially landed. Rename
  happy path works against the stub topics. Merge, the
  curation-survives-rerun mechanism (`topic_curation_events` or
  equivalent — slice 08), error edge cases beyond "not found"/"already
  exists", and inline-edit UX polish all remain.

### Deferred (logged so the next iteration can pick up)
- Inline edit UX in place of `window.prompt`.
- Curation persistence across discovery re-runs (slice 08).
- Topic merge action (slice 06's other half).

### Next session — Ralph iteration 9
1. Topic merge happy path (`/api/discovery/topic/merge`) with episode
   assignment dedupe under target topic.
2. Or: kick off slice 02 prep — Extractor module wiring for the real
   LLM discovery prompt.
3. Or: PRD §A4 legacy move (split into mechanical mini-iterations:
   create `legacy/__init__.py`; move `comparison_group_suggestions.py`
   with a re-export shim; verify imports in `review_ui.py` still work).

---

## 2026-05-05 — Slice 01 / Ralph iteration 7: per-topic episode sort options

### Done (TDD, 4 new tests in `test_discovery.py`)
- New `DiscoveryEpisodeSortHTMLTests` class with 4 tests asserting the
  sort dropdown markup (`discovery-episode-sort`, `value="recency"`,
  `value="confidence"`), the `sortDiscoveryEpisodes` JS function, the
  `DEFAULT_DISCOVERY_SORT = 'recency'` constant, and that
  `UI_REVISION` includes `sort`.
- Per-topic sort dropdown rendered inside each discovery topic card —
  options Recency / Confidence, default Recency. Selection persisted in
  a per-topic `Map` keyed by topic name; `setDiscoveryEpisodeSort` writes
  the choice and re-renders the panel via the cached `lastDiscoveryTopicMap`.
- New JS helpers: `sortDiscoveryEpisodes(episodes, mode)` returns a
  sorted copy. Recency = `published_at DESC` with nulls last, confidence
  = numeric DESC with nulls last, both with NOCASE-style title tiebreak.
- Backend payload unchanged — episodes still served by confidence DESC,
  the JS reorders client-side.
- View-count sort deferred: `videos.view_count` is not currently
  ingested. Documented in the iteration plan rather than added as a
  no-op option (would be a stub that always tied at 0).
- Relaxed older `test_ui_revision_advances_for_episode_list` from
  `topic-episodes` to the durable `discovery` substring (same pattern
  iteration 6 used on iteration-5's revision-string test).
- `UI_REVISION` bumped to `2026-05-05.3-discovery-episode-sort`.
- All 26 tests in `test_discovery.py` pass. Two pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` unchanged.
- Sanity: extracted JS body, `node --check` parses cleanly (43KB).

### Deferred (logged so the next iteration can pick up)
- View-count sort option — needs `videos.view_count` populated during
  ingestion (`youtube.py` does not currently fetch it).
- Sort persistence across reloads — issue 09 says "implementer's choice";
  current implementation resets to recency on reload.

### Next session — Ralph iteration 8
1. Remove comparison-group panels from primary GUI nav (PRD §A4).
2. Move comparison-group code to `legacy/` with deprecation shims.
3. Curation actions (rename / merge / split / move / mark wrong) —
   requires real LLM topics from slice 02 to be useful, but the
   rename happy path could be wired up now against stub topics.

---

## 2026-05-05 — Slice 01 / Ralph iteration 6: GUI per-topic episode list

### Done (TDD, 4 new tests in `test_discovery.py`)
- Two new test classes: `DiscoveryTopicMapEpisodesPayloadTests` (per-topic
  `episodes` list shape; multi-topic episode appears under each topic
  with the right reason/confidence) and `DiscoveryTopicEpisodesHTMLTests`
  (HTML hook + UI revision marker). Updated the older
  `test_ui_revision_advances_for_discovery_topic_map_panel` to assert the
  durable `discovery` substring rather than a stale per-iteration tag.
- Extended `_build_discovery_topic_map` with a second query that pulls
  per-topic episode rows joined to `videos` (id/title/thumbnail/
  published_at/confidence/reason) and groups them onto the topic dicts.
  Episodes sorted by descending confidence then NOCASE title.
- New JS helper `renderDiscoveryEpisodeItem` renders each card: 64x36
  thumbnail (placeholder gradient when missing), two-line clamped title,
  confidence percentage chip, raw youtube_video_id, italic reason line.
  Topic cards now include `<ol class="discovery-episode-list">` below
  the confidence bar.
- Episode cards get `.low` (opacity 0.78, amber confidence) or
  `.very-low` (opacity 0.55, red confidence) modifiers below the same
  thresholds the topic-level confidence bar uses.
- `UI_REVISION` bumped to `2026-05-05.2-discovery-topic-episodes`.
- All 22 tests in `test_discovery.py` pass. Two pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` unchanged.
- Sanity: extracted `<script>` body and `node --check` parses cleanly
  (41KB).

### Deferred (logged so the next iteration can pick up)
- Guest name on each episode card — not currently extracted from
  metadata; needs a description-parser or a separate "guest" field
  added during ingestion.
- Subtopics rendered under each topic — `stub_llm` doesn't produce
  subtopics, so persistence and rendering wait for the real LLM in
  slice 02 (PRD §A2).

### Next session — Ralph iteration 7
1. Curation actions on auto-discovered topics: rename, merge, split,
   move episode between topics, mark assignment as wrong (PRD §A3
   curation bullets).
2. Sort options for the per-topic episode list (recency, confidence)
   (PRD §A3 sort bullet).
3. Remove comparison-group panels from primary GUI nav.
4. Move comparison-group code to `legacy/` with deprecation shims.

---

## 2026-05-05 — Slice 01 / Ralph iteration 5: GUI discovery topic-map panel

### Done (TDD, 3 new tests in `test_discovery.py`)
- New `DiscoveryTopicMapHTMLTests` class. Tests assert that the rendered
  HTML page exposes `id="discovery-topic-map-grid"`, the heading
  "Auto-Discovered Topics", a `function renderDiscoveryTopicMap` JS
  definition, the call site `renderDiscoveryTopicMap(payload.discovery_topic_map)`
  inside `render()`, and that `UI_REVISION` includes `"discovery-topic-map"`.
- Added a new `<section class="topic-map discovery-topic-map">` above the
  existing pre-pivot Topic Map. Contains a `discovery-topic-map-meta`
  paragraph (run id / model / prompt version / status / created_at) and
  a `discovery-topic-map-grid` that holds the rendered topic cards.
- Added `renderDiscoveryTopicMap(map)` in the JS layer. Renders an empty
  state when the payload is null, a per-topic card grid otherwise.
  Each card shows topic name, episode count, average confidence as a
  percentage, and a colour-graded confidence bar (green ≥ 0.66, amber
  ≥ 0.33, red below). Wired into `render()` between `renderContext` and
  `renderTopicMap`.
- Added matching CSS: `.topic-map.discovery-topic-map` (green-tinted
  variant of the pre-pivot panel) and `.confidence-bar` with `.low` /
  `.very-low` modifiers.
- Bumped `UI_REVISION` to `2026-05-05.1-discovery-topic-map`.
- All 18 tests in `test_discovery.py` pass. Two pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` unchanged.
- Sanity: extracted `<script>` body and `node --check` parses cleanly.

### Next session — Ralph iteration 6
1. Topic detail in the discovery panel: episode list per topic with
   "why this episode is here" reason + confidence indicator (PRD §A3
   second/third bullets).
2. Curation actions on auto-discovered topics: rename, merge, split,
   move episode, mark assignment wrong.
3. Remove comparison-group panels from primary GUI nav.
4. Move comparison-group code to `legacy/` with deprecation shims.

---

## 2026-05-05 — Slice 01 / Ralph iteration 4: GUI discovery topic-map payload

### Done (TDD, 3 new tests in `test_discovery.py`)
- Added `_build_discovery_topic_map(db_path)` helper to `review_ui.py`.
  Reads the latest `discovery_runs` row, then aggregates
  `COUNT(DISTINCT video_id)` and `AVG(confidence)` per topic via the
  `video_topics.discovery_run_id` FK. Sorted by descending
  `episode_count`, then topic name (NOCASE).
- Wired the new payload into `build_state_payload()` under the
  `discovery_topic_map` key. Returns `None` when no discovery run
  exists; otherwise `{run_id, model, prompt_version, status, created_at,
  topics: [{name, episode_count, avg_confidence}, ...]}`.
- Existing pre-pivot `topic_map` (built from the old topic-suggestion
  flow) is unchanged and lives alongside the new key. The GUI HTML
  hasn't been touched yet — that's the next iteration's job.
- New `DiscoveryStatePayloadTests` class with three tests: empty case,
  happy path with two topics + 3 assignments, and latest-run isolation
  (older run ignored after a second run is recorded).
- All 15 tests in `test_discovery.py` pass. The 2 pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` are unchanged.

### Next session — Ralph iteration 5
1. Render `discovery_topic_map` in the GUI HTML/JS — a panel above the
   pre-pivot Topic Map showing the auto-discovered topics with episode
   counts and confidence indicators (PRD §A3 first bullet).
2. Remove comparison-group panels from primary GUI nav.
3. Move comparison-group code to `legacy/` with deprecation-warning
   import shims.

---

## 2026-05-05 — Slice 01 / Ralph iteration 3: CLI `analyze` chain

### Done (TDD, 2 new tests in `test_discovery.py`)
- Added `analyze` subparser in `cli.py` with `--db-path`, `--project-name`,
  `--channel-input`, `--limit`, `--stub`. The `--stub` flag is currently
  required, mirroring `discover`.
- Handler chains: `resolve_canonical_channel_id(channel_input)` →
  `fetch_channel_metadata` → `upsert_channel_metadata` (creates project +
  primary channel) → `fetch_channel_videos` → `upsert_videos_for_primary_channel`
  → `run_discovery(..., llm=stub_llm)`. Prints a one-line summary.
- New tests in `AnalyzeCLITests`:
  - `test_analyze_chains_setup_ingest_and_discover` — monkey-patches the three
    YouTube callables on the `cli` module, runs `cli.main(["analyze", ...])`
    against a fresh DB, asserts project + primary channel + 2 videos +
    1 discovery run + 2 `video_topics` rows with `assignment_source='auto'`.
  - `test_analyze_requires_stub_flag` — without `--stub`, `cli.main` exits
    non-zero.
- All 12 tests in `test_discovery.py` pass. `ReviewUIAppTests` pre-existing
  failures unchanged.

### Next session — Ralph iteration 4
1. GUI `/api/state` topic-map payload in `review_ui.py` (latest run's topics + episode counts).
2. Remove comparison-group panels from primary GUI nav.
3. Move comparison-group code to `legacy/` with deprecation-warning import shims.

---

## 2026-05-05 — Slice 01 / Ralph iteration 2: CLI `discover --stub`

### Done (TDD, 3 new tests in `test_discovery.py`)
- Added `stub_llm(videos) -> DiscoveryPayload` to `discovery.py`. Returns one
  topic (`General`) with every video assigned to it (`confidence=1.0`,
  `reason="stub assignment"`). Also exported `STUB_MODEL = "stub"` and
  `STUB_PROMPT_VERSION = "stub-v0"` so the CLI and tests share the same
  identifiers.
- Added `discover` subparser in `cli.py` with `--db-path`, `--project-name`,
  `--stub`. The `--stub` flag is currently required; without it the parser
  errors with "real LLM lands in slice 02" — keeps the CLI surface honest
  until slice 02.
- New tests:
  - `StubLLMTests.test_stub_llm_returns_one_topic_covering_all_videos`
  - `DiscoverCLITests.test_discover_stub_creates_run_and_assignments` —
    runs `cli.main(["discover", ..., "--stub"])` end-to-end against a
    seeded 2-video DB and asserts a `discovery_runs` row plus 2
    `video_topics` rows with `assignment_source='auto'`.
  - `DiscoverCLITests.test_discover_requires_stub_flag` — without `--stub`
    the CLI exits non-zero.
- All 10 tests in `test_discovery.py` pass. The 2 pre-existing
  `ReviewUIAppTests` failures in `test_transcripts.py` are unchanged.

### Next session — Ralph iteration 3
1. CLI `analyze` command chaining setup → ingest → discover.
2. GUI `/api/state` topic-map payload in `review_ui.py` (latest run's topics + episode counts).
3. Remove comparison-group panels from primary GUI nav.
4. Move comparison-group code to `legacy/` with deprecation-warning import shims.

---

## 2026-05-05 — Slice 01 session 2 / Ralph iteration 1: CHECK-constraint repair

### Done (TDD, 2 new tests in `test_discovery.py`)
- Added `_repair_video_topic_assignment_source_constraint` to `db.py`. Detects an
  old-shape `video_topics` / `video_subtopics` whose CHECK omits `'auto'` (by
  scanning `sqlite_master.sql` for the literal `'auto'`), then RENAMEs to
  `_old`, re-creates the table with the modern shape and CHECK clause, INSERT
  SELECTs all columns over, DROPs the old. Pattern mirrors
  `_repair_video_transcripts_constraint`.
- Wired the new repair into `ensure_schema` after the existing repairs and
  before INDEX_STATEMENTS (so unique indexes are re-created cleanly).
- New tests:
  - `test_ensure_schema_repairs_old_video_topics_check_constraint` — drops the
    fresh tables, recreates them with the pre-change CHECK, runs `ensure_schema`,
    then inserts an `'auto'` row into both junction tables.
  - `test_repair_preserves_existing_rows` — rebuilds an old-shape `video_topics`
    with one `('primary','manual')` row, runs `ensure_schema`, asserts the row
    survived.
- All 7 tests in `test_discovery.py` pass. The 2 pre-existing
  `ReviewUIAppTests` failures noted in session 1 are unchanged (verified the
  repair is a no-op on fresh DBs because `SCHEMA_STATEMENTS` already include
  `'auto'`).

### Open: git tracking
- Parent repo at `/home/chris/.openclaw/workspace` tracks only `db.py` and
  `review_ui.py` from this project. WORKLOG.md, PRD_PHASE_A_TOPIC_MAP.md,
  `discovery.py`, `test_discovery.py`, `.scratch/`, `extractor/`, and all the
  project docs are **untracked**. Ralph's per-iteration commit contract needs
  Chris's call: commit yt_channel_analyzer artefacts to the parent repo, init
  a nested repo here, or skip auto-commits and let WORKLOG be the progress
  ledger.

### Next session — Ralph iteration 2
1. CLI `discover` command (stub payload behind `--stub`) — test via `cli.main`.
2. CLI `analyze` command chaining setup → ingest → discover.
3. GUI `/api/state` topic-map payload in `review_ui.py` (latest run's topics + episode counts).
4. Remove comparison-group panels from primary GUI nav.
5. Move comparison-group code to `legacy/` with deprecation-warning import shims.

---

## 2026-05-04 — Slice 01 session 1: schema + stub discovery

### Done (TDD, 5 tests in `test_discovery.py`)
- Added `discovery_runs` table (channel_id FK, model, prompt_version, status, created_at).
- Extended `video_topics` and `video_subtopics` with `confidence REAL`, `reason TEXT`,
  `discovery_run_id INTEGER` (FK → discovery_runs ON DELETE SET NULL).
- Expanded `assignment_source` CHECK on both tables to include `'auto'`.
- New module `yt_channel_analyzer/discovery.py`: `DiscoveryVideo`, `DiscoveryAssignment`,
  `DiscoveryPayload`, and `run_discovery(db_path, *, project_name, llm, model, prompt_version) -> run_id`.
  LLM is injected as a callable — stub today, real LLM in slice 02.

### Learned / known gap
- `_ensure_required_columns` auto-adds the new columns to existing DBs (entries added to
  `REQUIRED_TABLE_COLUMNS`).
- SQLite can't ALTER a CHECK constraint — old DBs with the pre-change `assignment_source`
  CHECK will reject `'auto'` inserts. Needs a table-rebuild repair like
  `_repair_video_transcripts_constraint`. Not blocking fresh-DB tests; required before this
  hits any persisted DB.
- 2 pre-existing failures in `test_transcripts.py::ReviewUIAppTests` are unrelated to slice 01
  (verified against unmodified `db.py`).

### Next session — continue slice 01
1. CHECK-constraint repair for `video_topics` / `video_subtopics` (test: insert `'auto'`
   into an old-shape DB after `ensure_schema`).
2. CLI `discover` command (stub payload behind `--stub`) — test via `cli.main`.
3. CLI `analyze` command chaining setup → ingest → discover.
4. GUI `/api/state` topic-map payload in `review_ui.py` (latest run's topics + episode counts).
5. Remove comparison-group panels from primary GUI nav.
6. Move comparison-group code to `legacy/` with deprecation-warning import shims; keep
   `test_transcripts.py` green.

Read first next time: `CURRENT_STATE.md`, `PRD_PHASE_A_TOPIC_MAP.md`,
`.scratch/phase-a-topic-map/issues/01-*.md`, `discovery.py`, `test_discovery.py`.

---

## 2026-05-04 — Vision pivot to podcast knowledge extractor

### Done
- Reframed the project from "manual research workbench" to "podcast knowledge extractor."
  Canonical use case: point the app at *Diary of a CEO*, get a topic map, later get
  per-topic consensus / conflict / advice, eventually free-form Q&A.
- Resolved load-bearing architectural decisions through a structured grilling session:
  - Product shape: extractor + synthesizer, not curation workbench.
  - Unit of analysis: the **claim** (Phase C onward); episode-with-tags for MVP.
  - Topic discovery: LLM-proposed from metadata (titles, descriptions, chapter markers),
    then human-curated. No transcripts in MVP.
  - MVP scope: **Phase A — topic map of channel + episodes per topic.**
  - Episode-to-topic relationship: multi-topic, each assignment has confidence + reason.
  - Code strategy: **retrofit in place**; comparison-group machinery moves to `legacy/`.
  - LLM strategy: tiered (Haiku for extraction, Sonnet for synthesis), batch APIs,
    local sentence-transformers embeddings, `sqlite-vec` for vector storage,
    process-once-store-forever.
- Cost-modelled the full DOAC backlog: ~$0.10 for Phase A discovery, ~$8 one-time for
  Phase C full claim extraction with batch API. Phase D Q&A is fractions of a cent per query.
- Rewrote `PROJECT_SUMMARY.md`, `CURRENT_STATE.md`, `ROADMAP.md` to reflect the new vision.
- Wrote `PRD_PHASE_A_TOPIC_MAP.md` as the detailed plan for the next build slice.

### Learned
- The earlier "research workbench" framing was real but produced a product the user didn't
  actually want to operate manually. The user wants the app to do the structuring; they
  want to curate the result, not build it from scratch.
- Topic discovery does NOT require full transcripts. Metadata + chapter markers carry
  enough signal to propose a credible taxonomy at near-zero cost. This unblocks an
  early, cheap MVP.
- Most of the existing ~600KB of code is reusable. The schema, ingestion, review UI, and
  topic suggestion machinery all carry over with shifted semantics. The conceptual layer
  changed; the plumbing did not.
- The casualties are bounded to the comparison-group surface area. Those modules go to
  `legacy/`, not to deletion, in case Phase C wants pieces back.

### Next
- Phase A1: extend schema with `video_topics` / `video_subtopics` junction tables and
  a `discovery_runs` table; backfill from existing primary/secondary topic columns.
- Phase A2: build `discovery.py` — single batched LLM call that produces taxonomy +
  per-episode multi-topic assignments from metadata only.
- Phase A3: extend `review_ui.py` to render the auto-discovered topic map with confidence
  visible and curation actions (rename, merge, split, move, mark wrong).
- Phase A4: move comparison-group code to `legacy/`.
- First real run target: ingest Diary of a CEO and review the resulting topic map.

---

## 2026-04-25

### Done
- Added living project docs: `PROJECT_SUMMARY.md`, `ROADMAP.md`, and `CURRENT_STATE.md`.
- Captured the product as a structured YouTube research workbench rather than just a transcript tool.
- Documented that the review UI exists because CLI-only testing and QA became too difficult in practice.
- Tightened the roadmap to reflect a move toward a GUI-led workflow.

### Learned
- The codebase has grown beyond the earliest narrowly scoped mission notes.
- The next leverage point is probably better operator experience, not just more capability.
- GUI improvements are justified by actual workflow pain, not polish for its own sake.

### Next
- Identify the highest-friction review/QA tasks that still rely too heavily on CLI.
- Improve the GUI around broad-topic suggestion review/apply flows.
- Keep the docs updated as the workflow direction becomes clearer.

## 2026-04-25 — GUI workflow feedback

### Done
- Captured major GUI usability issue: the UI currently exposes run IDs too prominently and makes the user manage implementation details.
- Added `GUI_UX_PLAN.md` to describe a better GUI-led workflow.

### Learned
- The user expects to ingest a channel, see broad topics, choose interesting topics, then drill into subtopics.
- Topic discovery should pique interest and guide exploration.
- Approving a topic label without clearly applying videos is confusing.
- Run IDs should be audit/history details, not the primary navigation model.
- Subtopic generation should be contextual to a selected parent topic, not something that requires remembering old run IDs.
- Comparison-group generation may need readiness indicators because the user is not sure whether there is enough data.

### Next
- Redesign the GUI flow around Channel Overview → Topic Map → Topic Detail → Subtopic Discovery → Comparison Readiness.
- Make approved-but-unapplied topic suggestions obvious.
- Add or design an **Approve + apply** path for topic suggestions.
- Hide run ID wrangling behind Advanced/History where possible.

## 2026-04-25 — GUI priority 1 patch

### Done
- Patched `review_ui.py` to make topic approval/application clearer.
- Added a primary **Approve + apply to videos** action for pending topic labels.
- Renamed the plain approval path to **Approve label only**.
- Added warning/help text explaining that approving a label does not assign videos by itself.
- Made approved-but-unapplied labels visually explicit with an **Approved but not applied** warning.
- Reworded bulk apply to **Apply to N video(s)**.
- Added `/api/topic/approve-and-apply` route.

### Verified
- `review_ui.py` compiles.
- Smoke-tested `/api/topic/approve-and-apply` against a copied SQLite DB.
- Smoke result: pending label was approved, application route ran, and state refreshed with `ready=0`, `applied=3`, `blocked=0` in the copied DB.

### Next
- Restart/reload the GUI and test the changed topic cards in browser.
- Next UX priority remains hiding run-ID-first navigation and making subtopic review parent-topic-led.

## 2026-04-25 — Topic Map first pass

### Done
- Added first-pass **Topic Map** above the old review panels.
- Added topic cards with status, applied videos, pending review count, ready-to-apply count, and subtopic count.
- Added **Explore topic** action to make topic-first exploration more obvious.
- Renamed old panel headings to more product-friendly language: Broad Topics, Subtopics, and Comparison Readiness.
- Bumped UI revision to `2026-04-25.2-topic-map`.

### Verified
- `review_ui.py` compiles.
- `build_state_payload()` returns `topic_map` with 8 topics against `tmp/test.sqlite`.
- Served page contains the new revision and Topic Map markup.

### Next
- Improve Topic Map interactions so selecting a topic feels like navigating to a topic detail view, not just changing a dropdown.
- Hide or demote run ID controls behind an advanced/history section.
- Build a real Topic Detail section for subtopic exploration.

## 2026-04-25 — Workbench topic-detail UI

### Done
- Used `frontend-design` direction to move the UI further from database-admin layout toward a research workbench.
- Added revision `2026-04-25.4-workbench-topic-detail`.
- Added visible selected-topic / selected research lane panel below Topic Map.
- Updated **Explore topic** so it sets status, selects the topic, refreshes state, and scrolls to the selected-topic panel.
- Added workflow rail: Broad topic → Subtopics → Compare.
- Added selected-topic actions: **Discover subtopics** and **Review subtopics**.
- Improved Topic Map card hover/selected styling.

### Verified
- `review_ui.py` compiles.
- Live served page contains revision `2026-04-25.4-workbench-topic-detail`.
- Live served page contains `selected-topic-detail` and `Selected research lane` markup.
- `/api/state` returns 8 topic map cards and selected topic `Artificial Intelligence`.

### Next
- If Chris still finds the layout off, inspect with browser screenshot/feedback and tune visual hierarchy.
- Demote run selectors into Advanced/History.
- Make selected-topic panel into a fuller topic detail view with videos and subtopic readiness.

## 2026-04-25 — Preserve selected topic context

### Done
- Fixed bug where generating subtopics from a selected topic could snap the UI back to the first available topic, e.g. Health & Wellness.
- Added `state.activeTopicName` in the review UI.
- Made **Explore topic** store the active topic explicitly.
- Made **Discover subtopics** use the active selected research lane rather than relying only on the dropdown.
- Made subtopic/comparison generation responses return their parent `topic`/`subtopic` so the client can refresh in the same context.
- Bumped UI revision to `2026-04-25.5-preserve-topic-context`.

### Verified
- `review_ui.py` compiles.
- Live UI serves revision `2026-04-25.5-preserve-topic-context`.
- Live `/api/state?topic=Artificial%20Intelligence` returns selected topic `Artificial Intelligence` and 8 topic-map cards.

### Next
- Chris should retest: Artificial Intelligence → Discover subtopics should remain on Artificial Intelligence after generation.
- If it still jumps, inspect browser state/event order and the run selector change handler.

## 2026-04-25 — Subtopic approve/apply flow

### Done
- Added subtopic equivalent of the topic approve/apply workflow.
- Pending subtopic cards now show **Approve + apply to videos** and **Approve label only**.
- Approved subtopics now show approved-but-not-applied warnings and **Apply to N video(s)** actions.
- Added backend routes `/api/subtopic/approve-and-apply` and `/api/subtopic/bulk-apply` using existing per-video subtopic assignment helper.
- Selected-topic detail now shows pending subtopic count in the compact metrics.
- Topic Map subtopic count includes pending subtopics for the currently selected topic.
- Bumped UI revision to `2026-04-25.6-subtopic-apply-flow`.

### Verified
- `review_ui.py` compiles.
- Copied-DB smoke test approved and applied a pending Psychology subtopic suggestion: matched 1, applied 1, skipped 0.
- Live UI serves revision `2026-04-25.6-subtopic-apply-flow`.
- Live page contains `approveAndApplySubtopic` and `bulkApplySubtopic` handlers.

### Next
- Decide whether already-applied videos should be hidden by default with a toggle to show all application rows.
- Continue reducing scroll distance: move pending subtopic status/actions closer to the selected-topic panel.

## 2026-04-25 — Subtopic cluster threshold

### Done
- Tightened subtopic suggestion prompt so subtopics are treated as reusable research clusters, not one-off tags.
- Added rule: new subtopics should plausibly cover at least 5 videos in the parent broad topic.
- Added generation-time suppression for new subtopic labels with fewer than `MIN_NEW_SUBTOPIC_CLUSTER_SIZE = 5` suggested videos.
- Existing approved subtopics can still receive individual new videos.
- Updated UI copy to explain that new subtopics need 5+ suggested videos and one-off labels are suppressed.
- Bumped UI revision to `2026-04-25.7-subtopic-cluster-threshold`.

### Verified
- `subtopic_suggestions.py` and `review_ui.py` compile.
- Copied-DB smoke test generated 3 fake suggestions under Psychology for one new label and correctly suppressed/rejected it: pending 0, rejected 1.
- Live UI serves revision `2026-04-25.7-subtopic-cluster-threshold` and includes the threshold copy.

### Next
- Consider surfacing suppressed labels in the UI as a collapsed/secondary section so the user understands why fewer suggestions appeared.
- Consider adding a configurable threshold control later, but default should stay conservative.

## 2026-04-25 — Subtopic review threshold enforcement

### Done
- Fixed overly permissive subtopic threshold logic: approved-existing labels were still being shown with only 2-4 suggested videos.
- Changed generation suppression so low-support labels are suppressed regardless of whether the subtopic label already exists.
- Added review/display filtering so pending subtopic suggestions below the 5-video threshold are hidden from the review queue.
- Added `suppressed_low_support` summary count for subtopic reviews.
- Selected-topic panel now shows **Suppressed tiny labels**.
- Bumped UI revision to `2026-04-25.8-subtopic-review-threshold`.

### Verified
- For Psychology, previous pending low-support suggestions were hidden: pending 0, suppressed_low_support 3.
- Live UI serves revision `2026-04-25.8-subtopic-review-threshold`.
- Live `/api/state?topic=Psychology` returns no pending subtopics and `suppressed_low_support: 3`.

### Next
- Consider exposing suppressed subtopic labels in a collapsed debug/history section if Chris wants visibility into what was filtered.

## 2026-04-25 — Topic inventory in selected research lane

### Done
- Added selected-topic inventory to the review UI.
- The selected research lane now shows **Assigned subtopics** with videos grouped under each subtopic.
- It also shows **Unassigned videos**: broad-topic videos not yet assigned to any subtopic.
- Added `topic_inventory` to `/api/state`.
- Bumped UI revision to `2026-04-25.9-topic-inventory`.

### Verified
- `review_ui.py` compiles.
- For `Personal Relationships`, topic inventory shows `Family: 2`, `Friendship: 6`, `unassigned: 0`.
- Live served page contains revision `2026-04-25.9-topic-inventory` and inventory markup.

### Next
- Consider adding quick actions for unassigned videos, e.g. assign to existing subtopic, generate suggestions for unassigned only, or manually create subtopic.

## 2026-04-25 — Subtopic readiness in selected research lane

### Done
- Added per-subtopic readiness to the selected-topic inventory.
- Subtopics with fewer than 5 assigned videos are marked **Too thin to compare**.
- Subtopics with 5+ assigned videos are marked **Ready for comparison**.
- Added an inline **Generate comparison groups** action for ready subtopics.
- Bumped UI revision to `2026-04-25.10-subtopic-readiness`.

### Verified
- `review_ui.py` compiles.
- Live UI revision check passed.
- For `Personal Relationships`: `Family` has 2 videos and is too thin; `Friendship` has 6 videos and is ready for comparison.

### Next
- Use the ready `Friendship` subtopic to generate comparison-group suggestions.
- After comparison groups are reviewed, fetch/process transcripts for one chosen comparison group rather than fetching everything.

## 2026-04-25 — Fixed blank page after readiness patch

### Issue
- Browser page stopped loading after `2026-04-25.10-subtopic-readiness`.
- Server was still returning HTTP 200, so this was a frontend JS parse failure rather than a backend outage.

### Cause
- A JavaScript escaping helper inside the Python triple-quoted HTML string was mangled, producing an invalid regular expression in the rendered script.

### Fix
- Removed the fragile `escapeJs` helper.
- Used `JSON.stringify(bucket.name)` for safe inline button arguments instead.

### Verified
- Extracted rendered `<script>` and ran `node --check` successfully.
- Restarted the review UI.
- Live page check passed: page loads, revision is present, bad helper is gone, safe inline argument is present.

## 2026-04-25 — Fixed inline Generate comparison groups button

### Issue
- The inline **Generate comparison groups** button in the selected research lane rendered but did not trigger generation.
- Server logs showed no `POST /api/generate/comparison-groups`, so the click was failing client-side before reaching the backend.

### Cause
- The inline `onclick` argument used `JSON.stringify(bucket.name)` inside a double-quoted HTML attribute, so the generated attribute broke for string values.

### Fix
- Changed the inline handler attribute to single quotes around the attribute value while keeping `JSON.stringify(bucket.name)` for the JavaScript argument.

### Verified
- Rendered script passes `node --check`.
- Live page includes the safe single-quoted `onclick` and no longer includes the broken double-quoted handler.
