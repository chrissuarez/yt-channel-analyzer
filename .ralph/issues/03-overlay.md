# Slice 03 — Flip default + orphan counts + read-only UI badge

**Full spec (read it, follow acceptance criteria verbatim):** `.scratch/shorts-filter/issues/03-C-flip-default-ui.md`

Slice 3 of 3 in the shorts-filter feature. Slices A + B are merged to `main` — `videos.duration_seconds`, `channels.exclude_shorts` (currently `DEFAULT 0`), the 5 `discovery_runs` audit columns, the `discovery.SHORTS_CUTOFF_SECONDS=180` pre-LLM filter, and `discover --exclude-shorts/--include-shorts` all exist and work. **This is the slice that changes behavior for every channel** — after it merges, the next `discover` on any existing channel will have a different (cleaner) topic map. The badge + audit fields make that legible rather than mysterious. Design is **frozen** — do not relitigate. Background: `.scratch/shorts-filter/issues/` and memory `project_shorts_filter.md`.

## Scope this iteration

### 1. Migration + new default (`db.py`)
- Change `exclude_shorts` to `DEFAULT 1` in **both** `SCHEMA_STATEMENTS` (the `channels` CREATE TABLE) and `REQUIRED_TABLE_COLUMNS` (the `ADD COLUMN` fallback) so brand-new DBs and freshly-added columns start with the filter on.
- One-shot migration for **existing** DBs whose `channels` table still has `DEFAULT 0`: in `ensure_schema()`, add a repair function modeled on `_repair_video_topic_assignment_source_constraint` (same file) — inspect `sqlite_master.sql` for the `channels` table; if it contains `DEFAULT 0` for `exclude_shorts` (i.e. pre-slice-C), run `UPDATE channels SET exclude_shorts = 1` **once**, then rebuild the `channels` table with `DEFAULT 1` (RENAME → CREATE new → INSERT SELECT → DROP old, preserving all columns/constraints/FKs/UNIQUE — copy the existing `channels` definition verbatim except the one default). After the rebuild the create-SQL says `DEFAULT 1`, so the function is a no-op on every subsequent `ensure_schema()` — that's the idempotency guard (matches the existing pattern; do NOT introduce a marker table). Wire the call into `ensure_schema()` next to the other repair calls.
- **This rebuild touches the `channels` table → it's a "destructive migration" per the Ralph HITL triggers. Implement it, run the verify gate, then `HITL_PAUSE` for operator review of the migration before merge** — do not treat it as auto-mergeable.

### 2. Orphan counting (`discovery.py`)
In `run_discovery`, when the shorts filter is active (only then; leave NULL when off), after the filter runs and before the run row is finalized, compute and persist on the `discovery_runs` row:
- `n_orphaned_wrong_marks` — count of `wrong_assignments` rows for this channel whose `video_id` is a now-filtered video (one SQL query joining `wrong_assignments` against `videos.duration_seconds <= SHORTS_CUTOFF_SECONDS`). Scope to this channel.
- `n_orphaned_renames` — count of `topic_renames` (for this project) whose target topic loses **all** evidence in *this* run because every supporting episode was filtered. Compute by comparing the post-filter assignment set (the topics actually assigned this run) against the rename targets: a rename target counts as orphaned if no kept episode is assigned to it this run. (If the run's payload comes back before you can know assignments, compute after the assignment inserts, then `UPDATE` the run row.)
- These rows are **never deleted** — only counted. If the user later sets `exclude_shorts=0` and re-runs, the wrong-marks/renames wake back up. Acceptance includes verifying that.

### 3. Read-only UI badge (`review_ui.py`)
- In the discovery topic-map header (the `_DISCOVERY_*` helpers / `build_state_payload()` envelope), surface a one-line read-only badge after the run header: `"X shorts excluded · Y curation actions inert (target episodes filtered)"` where Y = `n_orphaned_wrong_marks + n_orphaned_renames`.
- **Hide the badge entirely** when `n_shorts_excluded == 0 && n_orphaned_wrong_marks == 0 && n_orphaned_renames == 0` (no noise on channels with no Shorts).
- Pure render-side: no new endpoint, no write path, no toggle. Build off the existing `build_state_payload()` envelope; add the audit counts to the discovery-topic-map payload block if they aren't already there.
- **HARD CAP: ≤ 200 lines of net change in `review_ui.py`.** The Ralph harness HITL-pauses at 300. If your `review_ui.py` change is creeping past ~200 net lines, STOP and `HITL_PAUSE` asking how to trim — do not push through.
- Bump `UI_REVISION` keeping the existing `channel-overview` + `discovery` substrings (so the `test_ui_revision_advances_for_*` assertions stay green); add a `shorts-filter` substring so this slice's HTML tests can pin against it.

### 4. Tests
- `test_discovery.py`: orphan counts populated correctly when the filter excludes videos that have wrong-marks against them; orphan counts populated when a rename's target loses all evidence post-filter; orphan counts are 0 (or NULL — match what the code writes) when the filter is off; wrong-marks/renames survive the filter (re-run with `exclude_shorts=0` → they're back).
- `test_discovery.py` (or a lightweight `review_ui` test): the discovery payload carries the audit counts when `n_shorts_excluded > 0`; the badge string appears in rendered HTML when counts are non-zero and is absent when all zero.
- Migration test: `ensure_schema()` on a DB whose `channels` table has `DEFAULT 0` flips every channel to `exclude_shorts=1` exactly once and leaves the create-SQL saying `DEFAULT 1`; running `ensure_schema()` again does not re-flip a channel that was manually set back to 0.

## Out of scope
- A writable per-channel toggle in the UI — explicitly deferred. CLI flag + manual SQL is the v1 story for changing a channel's setting after migration.
- Dedup-style short→parent detection (Phase C concern at earliest).
- A separate settings panel.

## Constraints / HITL triggers (this slice has several — read carefully)
- **Destructive migration** (the `channels` table rebuild) → implement, verify green, then `HITL_PAUSE` for operator review before merge.
- **`review_ui.py` net change must stay ≤ 200 lines** (harness pauses at 300). If it grows, `HITL_PAUSE`.
- **Real-LLM verify is operator-only** → after code + offline gate are green, end with `HITL_PAUSE` flagging: operator re-runs plain `discover --real` on a real channel with `RALPH_ALLOW_REAL_LLM=1`, confirms the topic map is cleaner than the prior run, audit + orphan counts populated, badge renders on `:8765`. Do NOT call the real API yourself.
- `.ralph/verify.sh` (discovery + extractor, offline) must stay green every iteration.
- Update `WORKLOG.md` with a one-liner about the default flip so future iterations see why DOAC's topic map shape changed. Flip the slice-C checkbox in ROADMAP `§A9`.
- Conventional commits; end commit messages with the `Co-Authored-By:` trailer.

Given the three HITL conditions above, it's very likely this slice ends in a `HITL_PAUSE` after one or two iterations — that's expected, not a failure.
