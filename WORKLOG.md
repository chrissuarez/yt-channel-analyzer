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
